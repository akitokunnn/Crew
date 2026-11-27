"""Leader-specific trust operations in an isolated workspace."""
import tests
from test_core import operation_service
from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from pathlib import Path
import tempfile
import unittest
from crew import board, store, trust_mode


class LeaderTrustCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home, self.root = self.base / 'home', self.base / 'repo'
        self.root.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, 'demo', self.root)
        for sid in ('a','b'):
            board.join(self.conn,sid,'demo',sid,self.root,self.root,'main',role='leader')
        board.join(self.conn,'member','demo','worker',self.root,self.root,'main',group_name='a')
        self.service = operation_service(self, self.root)

    def test_human_must_choose_leader_and_settings_do_not_activate_sessions(self):
        with self.assertRaises((OpError, ValueError, board.BoardError)):
            self.service.op('trust.set', {'enabled': True, 'goal': 'goal'}, project='demo')
        value = self.service.op('trust.set', {'enabled': True, 'goal': 'A target', 'leader_session_id': 'a'}, project='demo')
        self.assertEqual(value['leader_session_id'],'a')
        self.assertTrue(value['enabled'])
        self.assertFalse(trust_mode.get(self.conn,'demo','b')['enabled'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        self.assertEqual(board.session(self.conn,'a')['status'],'idle')

    def test_member_defaults_to_own_leader_and_cannot_choose_another_group(self):
        value = ai_call(self.service, self.conn, 'member', 'trust.get')
        self.assertEqual(value['leader_session_id'], 'a')
        with self.assertRaises((OpError, board.BoardError)):
            ai_call(self.service, self.conn, 'member', 'trust.get', {'leader_session_id': 'b'})
        with self.assertRaises((OpError, board.BoardError)):
            ai_call(self.service, self.conn, 'a', 'trust.set', {'enabled': True, 'goal': 'wrong', 'leader_session_id': 'b'})
        with self.assertRaises((ValueError, OpError, board.BoardError)):
            self.service.op('trust.get', {'leader_session_id': 'member'}, project='demo')


if __name__=='__main__':unittest.main()
