"""Leader-specific trust CLI, temporary homes and no service/model startup."""
import tests
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, cli, store, trust_mode


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
        environment=mock.patch.dict(os.environ,{'CREW_SESSION_ID':'','CLAUDE_CODE_SESSION_ID':''})
        environment.start();self.addCleanup(environment.stop)

    def command(self, *args, session=None, expected=0):
        out,err=io.StringIO(),io.StringIO()
        prefix=['--home',str(self.home)]
        if session:prefix.extend(['--session',session])
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=cli.main(prefix+['trust',*args],cwd=self.root,agents_provider=lambda:self.fail('No discovery or activation'))
        self.assertEqual(code,expected,err.getvalue())
        return json.loads(out.getvalue()) if code==0 else err.getvalue()

    def test_human_must_choose_leader_and_settings_do_not_activate_sessions(self):
        self.command('set','on','--goal','goal',expected=1)
        value=self.command('set','on','--goal','A target','--leader','a','--json')
        self.assertEqual(value['leader_session_id'],'a')
        self.assertTrue(value['enabled'])
        self.assertFalse(trust_mode.get(self.conn,'demo','b')['enabled'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        self.assertEqual(board.session(self.conn,'a')['status'],'idle')

    def test_member_defaults_to_own_leader_and_cannot_choose_another_group(self):
        value=self.command('status','--json',session='member')
        self.assertEqual(value['leader_session_id'],'a')
        self.command('status','--leader','b','--json',session='member',expected=1)
        self.command('set','on','--goal','wrong','--leader','b',session='a',expected=1)
        self.command('status','--leader','member','--json',expected=1)


if __name__=='__main__':unittest.main()
