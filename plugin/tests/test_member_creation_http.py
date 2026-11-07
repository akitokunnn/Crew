"""Owner member creation over real loopback HTTP with fake native processes."""
import tests
import threading
import unittest

from crew import board, member_defaults, models, runtimes, store, web
import test_chat_http
from test_shell import NOW, PLUGIN, ShellFixture


class MemberCreationHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = self.server.url
        self.choice = {'runtime':'claude','model':'claude-sonnet-5-5','effort':'high'}

    def test_owner_can_save_defaults_and_create_a_member_by_stable_leader_id_after_rename(self):
        code, value, _ = self.request('/api/member-defaults?project=demo', {'leader_session_id':'s3','selection':self.choice})
        self.assertEqual(code,200,value)
        self.assertEqual(value['selection'],self.choice)
        self.assertEqual(self.processes,[])
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(next(leader for leader in state['leaders'] if leader['session_id']=='s3')['member_defaults'],self.choice)
        code, value, _ = self.request('/api/session/rename?project=demo', {'session_id':'s3','name':'renamed leader'})
        self.assertEqual(code,200,value)
        code, value, _ = self.request('/api/spawn?project=demo', {'leader_session_id':'s3','name':'new owner member','task':'Finite task with a result','role':'member'})
        self.assertEqual(code,200,value)
        sid=value['session_id']
        self.assertEqual(board.session(self.conn,sid)['group_name'],'renamed leader')
        self.assertEqual(runtimes.get(self.conn,sid)['runtime'],'claude')
        self.assertEqual(models.get(self.conn,sid),{'model':'claude-sonnet-5-5','effort':'high'})
        self.assertEqual(len(self.processes),1)
        self.assertFalse(self.server.service.chat.running('s3'))

    def test_member_defaults_and_owner_creation_reject_wrong_identity_and_foreign_project(self):
        other=self.root/'other';other.mkdir()
        board.init_project(self.conn,'other',other)
        board.join(self.conn,'foreign-chief','other','foreign',other,None,None,role='leader')
        for sid in ('s1','foreign-chief'):
            code,value,_=self.request('/api/member-defaults?project=demo',{'leader_session_id':sid,'selection':self.choice})
            self.assertEqual(code,400,value)
            code,value,_=self.request('/api/spawn?project=demo',{'leader_session_id':sid,'name':'bad child','task':'finite'})
            self.assertEqual(code,400,value)
        code,value,_=self.request('/api/spawn?project=demo',{'leader_session_id':'s3','group':'wrong','name':'bad child','task':'finite'})
        self.assertEqual(code,400,value)
        self.assertEqual(self.processes,[])
        self.assertIsNone(member_defaults.get(self.conn,'s3'))

    def test_member_cannot_spawn_or_create_leader_and_adopt_existing_members(self):
        before = self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        for path, extra in (('/api/spawn', {'leader_session_id': 's3'}),
                            ('/api/leader', {'members': ['two']})):
            with self.subTest(path=path):
                code, value, _ = self.request(path + '?project=demo', {
                    'caller_session_id': 's1', 'name': 'nested', 'task': 'Finite task', **extra})
                self.assertEqual(code, 400, value)
                self.assertIn('只有本项目当前 leader', value['error'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], before)
        self.assertIsNone(board.session(self.conn, 's2')['group_name'])
        self.assertEqual(self.processes, [])

    def test_owner_can_create_a_leader_without_inventing_an_ai_identity(self):
        code, value, _ = self.request('/api/leader?project=demo', {
            'name': 'owner leader', 'task': 'Finite task', 'members': []})
        self.assertEqual(code, 200, value)
        self.assertEqual(board.session(self.conn, value['session_id'])['role'], 'leader')
        self.assertEqual(len(self.processes), 1)

    def test_leader_can_create_member_but_cannot_create_another_leader(self):
        code, value, _ = self.request('/api/spawn?project=demo', {
            'caller_session_id': 's3', 'name': 'direct member', 'task': 'Finite task'})
        self.assertEqual(code, 200, value)
        self.assertEqual(board.session(self.conn, value['session_id'])['group_name'], 'lead')
        code, value, _ = self.request('/api/leader?project=demo', {
            'caller_session_id': 's3', 'name': 'nested leader', 'task': 'Finite task', 'members': []})
        self.assertEqual(code, 400, value)
        self.assertIn('只能在自己的当前组创建成员', value['error'])
        self.assertEqual(len(self.processes), 1)
