"""Complete assistant input/tool paths through fake CC and Codex carriers."""
import tests
from contextlib import closing
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, chat, cli, codex_runtime, codex_tools, personal, runtimes, settings, store, workspace_execution
from crew.web_api import personal as personal_api
from test_chat import FakeProcess
from test_codex_runtime import FakeClient


class PersonalNativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-personal-native-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home'
        self.plugin = Path(__file__).resolve().parents[1]
        self.environment = mock.patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
            os.environ.pop(key, None)

    def assistant(self, runtime):
        with closing(store.connect(self.home)) as conn:
            return personal.ensure_assistant(conn, self.home, selection={'runtime': runtime, 'model': None, 'effort': None})

    def manager(self):
        manager = chat.Manager(self.home, self.plugin, lambda: [], process_factory=FakeProcess)
        self.addCleanup(manager.close)
        return manager

    def test_assistant_execution_is_independent_of_workspace_and_missing_cwd_is_visible(self):
        value = self.assistant('codex')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(workspace_execution.execution_session(conn, value)['cwd'], value['cwd'])
            self.assertIsNone(workspace_execution.migration_notice(conn, value))
            Path(value['cwd']).rmdir()
            with self.assertRaises(FileNotFoundError):
                workspace_execution.execution_session(conn, value)

    def test_codex_chat_loads_only_personal_tools_and_binds_real_user_input(self):
        assistant = self.assistant('codex')
        manager = self.manager()
        manager.journal(assistant['session_id']).set_status('idle')
        clients = []
        def factory(**kwargs):
            clients.append(FakeClient(**kwargs))
            return clients[-1]
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=factory):
            result = manager.send(assistant, '记下我想做一个健身应用', identity='user-1')
        self.assertTrue(result['accepted'])
        client = clients[0]
        start = next(data for method, data in client.calls if method == 'thread/start')
        self.assertEqual({tool['name'] for tool in start['dynamicTools']}, {'crew_personal', 'crew_whoami'})
        self.assertIn('整个 Crew', start['developerInstructions'])
        self.assertFalse(start['config']['agents']['enabled'])
        turn = next(data for method, data in client.calls if method == 'turn/start')
        self.assertIn('user-1', json.dumps(turn, ensure_ascii=False))
        event = next(event for event in manager.history(assistant['session_id'])['events'] if event['type'] == 'message')
        self.assertEqual(event['text'], '记下我想做一个健身应用')
        handler = client.kwargs['tool_handler']
        response = handler({'threadId': client.thread_id, 'callId': 'capture', 'tool': 'crew_personal',
            'arguments': {'action': 'note.capture', 'message_id': 'user-1', 'request_id': 'capture-1',
                          'data': {'text': '想做一个健身应用'}}})
        self.assertTrue(response['success'], response)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(personal.list_notes(conn)[0]['text'], '想做一个健身应用')
            self.assertEqual(personal.projects(conn), [])
        denied = handler({'threadId': client.thread_id, 'callId': 'spawn', 'tool': 'crew_spawn',
                          'arguments': {'name': 'child', 'task': '绕开 leader'}})
        self.assertFalse(denied['success'])
        client.finish()

    def test_claude_chat_loads_assistant_rules_and_keeps_original_journal(self):
        assistant = self.assistant('claude')
        manager = self.manager()
        manager.journal(assistant['session_id']).set_status('idle')
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            result = manager.send(assistant, '你好', identity='human-cc')
        self.assertTrue(result['accepted'])
        process = manager.processes[assistant['session_id']].process
        self.assertIn('--append-system-prompt', process.command)
        prompt = process.command[process.command.index('--append-system-prompt') + 1]
        self.assertIn('crew personal', prompt)
        self.assertIn('整个 Crew', prompt)
        inputs = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        user = next(value for value in inputs if value['type'] == 'user')
        self.assertIn('human-cc', user['message']['content'])
        journal = manager.history(assistant['session_id'])
        self.assertEqual(next(item['text'] for item in journal['events'] if item['type'] == 'message'), '你好')
        self.assertEqual(process.options['cwd'], assistant['cwd'])

    def test_regular_codex_member_cannot_use_global_assistant_tool(self):
        self.assistant('codex')
        with closing(store.connect(self.home)) as conn:
            root = self.home.parent / 'folder'
            root.mkdir()
            board.init_project(conn, 'folder', root)
            board.register_session(conn, 'member', 'folder', root, None, None, name='member')
            from crew import runtimes
            runtimes.set(conn, 'member', 'codex')
        handler = codex_tools.CodexTools(self.home, 'member', personal_handler=mock.Mock())
        result = handler({'tool': 'crew_personal', 'arguments': {'action': 'summary'}})
        self.assertFalse(result['success'])

    def test_claude_assistant_first_reply_confirms_identity_then_next_launch_resumes_it(self):
        assistant = self.assistant('claude')
        sid = assistant['session_id']
        manager = self.manager()
        manager.journal(sid).set_status('idle')
        with closing(store.connect(self.home)) as conn:
            self.assertIsNone(runtimes.get(conn, sid)['native_id'])
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            manager.send(assistant, 'First message', identity='first-cc')
        first = manager.processes[sid].process
        self.assertNotIn('--resume', first.command)
        self.assertEqual(first.command[first.command.index('--session-id') + 1], sid)
        first.emit({'type':'system','subtype':'init','session_id':sid})
        first.emit({'type':'assistant','session_id':sid,
                    'message':{'id':'confirmed-reply','content':[{'type':'text','text':'First answer'}]}})
        first.emit({'type':'result','session_id':sid,'is_error':False})
        journal=manager.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status=='idle' and 'confirmed-reply' in journal.assistant, timeout=3))
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(runtimes.get(conn,sid)['native_id'],sid)
        manager.stop_managed(sid)
        self.assertTrue(manager.processes[sid].reader_done.wait(3))
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            manager.send(assistant, 'Second message', identity='second-cc')
        resumed=manager.processes[sid].process
        self.assertEqual(resumed.command[resumed.command.index('--resume')+1],sid)
        self.assertNotIn('--session-id',resumed.command)

    def test_untouched_031_claude_assistant_prefilled_identity_can_start_once(self):
        assistant=self.assistant('claude')
        sid=assistant['session_id']
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn,sid,'claude',sid)
        manager=self.manager()
        manager.journal(sid).set_status('idle')
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            manager.send(assistant,'First message',identity='legacy-first')
        self.assertNotIn('--resume',manager.processes[sid].process.command)

    def test_claude_assistant_with_existing_reply_must_resume_saved_history(self):
        assistant=self.assistant('claude')
        sid=assistant['session_id']
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn,sid,'claude',sid)
        manager=self.manager()
        manager.journal(sid).emit('assistant',id='earlier-reply',content=[{'type':'text','text':'Earlier answer'}])
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            manager.send(assistant,'Continue',identity='legacy-continue')
        command=manager.processes[sid].process.command
        self.assertEqual(command[command.index('--resume')+1],sid)
        self.assertNotIn('--session-id',command)
        self.assertNotIn('crew_personal', {item['name'] for item in codex_tools.tool_specs()})
        self.assertIn('crew_whoami', {item['name'] for item in codex_tools.tool_specs()})

    def test_authorized_assistant_starts_one_real_leader_flow_in_a_plain_folder(self):
        assistant = self.assistant('claude')
        manager = self.manager()
        manager.journal(assistant['session_id']).message('user-request', '请在 fitness 新开一个对话讨论健身打卡应用')
        root = self.home.parent / 'fitness'
        root.mkdir()
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'fitness', root)
            settings.save(conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        envelope = {'message_id': 'user-request', 'request_id': 'start-fitness',
                    'data': {'project': 'fitness', 'message': '讨论简单的健身打卡应用', 'name': 'fitness-plan'}}
        with mock.patch('crew.settings.shutil.which', return_value='fake-claude'), \
                mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            first = manager.personal_action('leader.create', envelope, assistant['session_id'])
            again = manager.personal_action('leader.create', envelope, assistant['session_id'])
        sid = first['result']['session_id']
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['result']['session_id'], sid)
        self.assertEqual(len(manager.processes), 1)
        with closing(store.connect(self.home)) as conn:
            leader = board.session(conn, sid)
            self.assertEqual((leader['role'], leader['project']), ('leader', 'fitness'))
            self.assertEqual([item['name'] for item in personal.projects(conn)], ['fitness'])
            self.assertIsNone(board.session(conn, assistant['session_id'])['project'])
        briefing = next(event['text'] for event in manager.history(sid)['events'] if event['type'] == 'message')
        self.assertIn('原始用户要求', briefing)
        self.assertIn('请在 fitness 新开一个对话讨论健身打卡应用', briefing)
        self.assertFalse((root / '.git').exists())

    def test_cli_reads_without_project_and_mutations_go_to_bound_host(self):
        assistant = self.assistant('claude')
        os.environ['CREW_SESSION_ID'] = assistant['session_id']
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            code = cli.main(['--home', str(self.home), 'personal', 'summary', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['projects'], [])
        request = mock.Mock(return_value={'ok': True})
        with mock.patch('crew.spawn.request_start', request), mock.patch('sys.stdout', io.StringIO()):
            code = cli.main(['--home', str(self.home), 'personal', 'note.capture', '--message-id', 'u',
                             '--request-id', 'r', '--data', '{"text":"想法"}'])
        self.assertEqual(code, 0)
        self.assertEqual(request.call_args.args[2], '/api/assistant/native-action')
        self.assertEqual(request.call_args.args[3]['actor_session_id'], assistant['session_id'])
        with mock.patch('sys.stderr', io.StringIO()):
            code = cli.main(['--home', str(self.home), '--session', 'other', 'personal', 'summary'])
        self.assertNotEqual(code, 0)

    def test_api_open_and_state_do_not_resolve_or_launch_native(self):
        with mock.patch('crew.settings.shutil.which', side_effect=AssertionError('opening must not discover CLI')):
            assistant = personal_api.open_assistant(self.home)
            manager = self.manager()
            value = personal_api.state(self.home, history=manager.history, activity=manager.activity,
                                       applied=manager.applied, context=manager.context)
        self.assertEqual(value['assistant']['session_id'], assistant['session_id'])
        self.assertEqual(value['members'], [value['assistant']])
        self.assertEqual(value['leaders'], [])
        self.assertEqual(value['projects'], [])
        self.assertFalse(value['assistant']['busy'])
        self.assertEqual(value['assistant']['last_seq'], 0)


if __name__ == '__main__':
    unittest.main()
