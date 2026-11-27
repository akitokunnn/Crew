"""Complete assistant input/tool paths through fake CC and Codex carriers."""
import tests
from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, web, personal, runtimes, settings, store, workspace_execution
from crew.web_api import personal as personal_api
from test_chat import FakeProcess
from test_codex_runtime import FakeClient


class PersonalNativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-personal-native-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home'
        self.plugin = Path(__file__).resolve().parents[1]
        self.service = web.Service(self.home, self.plugin, cwd=self.home.parent, agents_provider=lambda: [],
                                   process_factory=FakeProcess, isolated_sources=True)
        self.addCleanup(self.service.close)

    def assistant(self, runtime):
        with closing(store.connect(self.home)) as conn:
            return personal.ensure_assistant(conn, self.home, selection={'runtime': runtime, 'model': None, 'effort': None})

    def manager(self):
        return self.service.chat

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
        self.assertNotIn('dynamicTools', start)
        self.assertIn('整个 Crew', start['developerInstructions'])
        self.assertFalse(start['config']['agents']['enabled'])
        turn = next(data for method, data in client.calls if method == 'turn/start')
        self.assertIn('user-1', json.dumps(turn, ensure_ascii=False))
        event = next(event for event in manager.history(assistant['session_id'])['events'] if event['type'] == 'message')
        self.assertEqual(event['text'], '记下我想做一个健身应用')
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'fitness', self.home.parent)
            ai_call(self.service, conn, assistant['session_id'], 'note.capture',
                    {'project': 'fitness', 'data': {'text': '想做一个健身应用'}}, message_id='user-1', runtime='codex')
            self.assertEqual(personal.list_notes(conn, project='fitness')[0]['text'], '想做一个健身应用')
            self.assertIsNone(board.session(conn, assistant['session_id'])['project'])
            with self.assertRaises(OpError):
                ai_call(self.service, conn, assistant['session_id'], 'execution.spawn',
                        {'name': 'child', 'task': '绕开 leader'}, runtime='codex')
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
        self.assertIn('workspace_state', prompt)
        self.assertIn('整个 Crew', prompt)
        inputs = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        user = next(value for value in inputs if value['type'] == 'user')
        self.assertIn('human-cc', user['message']['content'])
        journal = manager.history(assistant['session_id'])
        self.assertEqual(next(item['text'] for item in journal['events'] if item['type'] == 'message'), '你好')
        self.assertEqual(process.options['cwd'], assistant['cwd'])

    def test_workspace_first_send_in_both_carriers(self):
        from crew import channel, git_admin, workspace_assistant
        from crew.ops import executor
        for runtime in ('claude', 'codex'):
            root = self.home.parent / runtime
            root.mkdir()
            with closing(store.connect(self.home)) as conn:
                board.init_project(conn, runtime, root)
                opened = workspace_assistant.ensure(conn, self.home, runtime)
                self.assertFalse(opened['configured'])
                settings.save(conn, {'assistant': {'runtime': runtime, 'model': None, 'effort': None},
                                     'default': {'runtime': None, 'model': None, 'effort': None}})
            sid = opened['session_id']
            clients = []

            def factory(**kwargs):
                clients.append(FakeClient(**kwargs))
                return clients[-1]

            with (mock.patch('crew.codex_runtime._ObservedClient', side_effect=factory),
                  mock.patch('crew.settings.shutil.which', return_value='fake-carrier'),
                  mock.patch('crew.chat.claude_executable', return_value='fake-claude'),
                  mock.patch('crew.ops.launch.endpoint', return_value=('http://127.0.0.1:1/mcp', 'core')),
                  mock.patch.object(git_admin, 'input_context', wraps=git_admin.input_context) as git,
                  mock.patch.object(channel, 'preview', wraps=channel.preview) as preview):
                sent = executor.human_call(self.service, 'chat.send', {'session_id': sid, 'text': '本区进展',
                                           'message_id': 'workspace-user-' + runtime}, project=runtime)
                self.assertTrue(sent['accepted'])
                self.assertEqual(git.call_args.args[1], sid)
                if runtime == 'claude':
                    proc = self.service.chat.processes[sid].process
                    self.assertEqual(Path(proc.options['cwd']), root)
                    self.assertIn('本工作区的助手', ' '.join(proc.command))
                    self.assertIn('--mcp-config', proc.command)
                    body = proc.stdin.getvalue()
                else:
                    client = clients[0]
                    start = next(params for name, params in client.calls if name == 'thread/start')
                    self.assertEqual(Path(start['cwd']), root)
                    self.assertIn('本工作区的助手', start['developerInstructions'])
                    self.assertIn('crew', start['config']['mcp_servers'])
                    body = json.dumps(next(params for name, params in client.calls if name == 'turn/start'))
                    self.assertEqual(preview.call_args.args[1], sid)
                    client.finish()
                self.assertIn('workspace-user-' + runtime, body)
                with closing(store.connect(self.home)) as conn:
                    grants = conn.execute('SELECT role,project,runtime FROM mcp_tokens WHERE session_id=?', (sid,))
                    self.assertEqual([tuple(row) for row in grants], [('workspace_assistant', runtime, runtime)])
                    settings.save(conn, {'default': {'runtime': None, 'model': None, 'effort': None}, 'assistant': None})

    def test_admin_saved_codex_thread_resumes_after_workspace_assistants_open(self):
        from crew import workspace_assistant
        assistant = self.assistant('codex')
        sid = assistant['session_id']
        manager = self.manager()
        with closing(store.connect(self.home)) as conn:
            root = self.home.parent / 'registered'
            root.mkdir()
            board.init_project(conn, 'work', root)
            workspace_assistant.ensure(conn, self.home, 'work')
            runtimes.set(conn, sid, 'codex', 'admin-existing-thread')
        manager.journal(sid).message('old-user', '旧问题')
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=FakeClient):
            manager.send(assistant, '继续', identity='new-user')
        client = manager.processes[sid].client
        resumed = next(params for name, params in client.calls if name == 'thread/resume')
        self.assertEqual(resumed['threadId'], 'admin-existing-thread')
        self.assertTrue(any(e.get('id') == 'old-user' for e in manager.history(sid)['events']))
        client.finish()

    def test_regular_codex_member_cannot_use_global_assistant_tool(self):
        self.assistant('codex')
        with closing(store.connect(self.home)) as conn:
            root = self.home.parent / 'folder'
            root.mkdir()
            board.init_project(conn, 'folder', root)
            board.register_session(conn, 'member', 'folder', root, None, None, name='member')
            from crew import runtimes
            runtimes.set(conn, 'member', 'codex')
            with self.assertRaises(OpError) as denied:
                ai_call(self.service, conn, 'member', 'session.detail', {'session_id': 'member'}, runtime='codex')
            self.assertEqual(denied.exception.code, 'forbidden')

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
            with closing(store.connect(self.home)) as conn:
                def create():
                    return ai_call(self.service, conn, assistant['session_id'], 'leader.create', envelope['data'],
                                   message_id=envelope['message_id'], request_id=envelope['request_id'])
                first = create()
                again = create()
        sid = first['result']['session_id']
        self.assertTrue(again['replayed'])
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

    def test_assistant_reads_without_project_and_mutations_bind_to_original_message(self):
        sid = self.assistant('claude')['session_id']
        self.service.chat.journal(sid).message('u', '请记下想法')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(ai_call(self.service, conn, sid, 'workspace.state')['projects'], [])
            board.init_project(conn, 'fitness', self.home.parent)
            args = {'project': 'fitness', 'data': {'text': '想法'}}
            with self.assertRaises((OpError, board.BoardError)):
                ai_call(self.service, conn, sid, 'note.capture', args, message_id='missing')
            ai_call(self.service, conn, sid, 'note.capture', args, message_id='u', request_id='r')
            self.assertEqual([n['text'] for n in personal.list_notes(conn, project='fitness')], ['想法'])

    def test_api_open_and_state_do_not_resolve_or_launch_native(self):
        with mock.patch('crew.settings.shutil.which', side_effect=AssertionError('opening must not discover CLI')):
            assistant = personal_api.open_assistant(self.home)
            manager = self.manager()
            value = personal_api.state(self.home, history=manager.history, journal=manager.journal, activity=manager.activity,
                                       applied=manager.applied, context=manager.context)
        self.assertEqual(value['assistant']['session_id'], assistant['session_id'])
        self.assertEqual(value['members'], [value['assistant']])
        self.assertEqual(value['leaders'], [])
        self.assertEqual(value['projects'], [])
        self.assertFalse(value['assistant']['busy'])
        self.assertEqual(value['assistant']['last_seq'], 0)


if __name__ == '__main__':
    unittest.main()
