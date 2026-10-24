"""Stale Claude hooks may not act on the new runtime's identity or mail."""
import tests
import contextlib
import io
from unittest import mock

from crew import board, hooks, messaging, runtimes
from test_core import CoreCase


class InactiveRuntimeTests(CoreCase):
    def switch_to_codex(self):
        runtimes.set(self.conn,'s1','codex','native-new')
        board.set_status(self.conn,'s1','working','new Codex turn')
        messaging.queue(self.conn,'s2','s1','message reserved for the current carrier')

    def test_old_claude_metadata_is_noop_before_install_state_or_mail(self):
        self.switch_to_codex()
        before=board.session(self.conn,'s1')
        changes=self.conn.total_changes
        for event in ('session-start','user-prompt-submit','stop','notification','pre-ask','post-ask','session-end'):
            with self.subTest(event=event), \
                 mock.patch('crew.store.connect',side_effect=AssertionError('must not migrate or write')), \
                 mock.patch('crew.install.install',side_effect=AssertionError('stale startup cannot refresh install')):
                self.assertEqual(self.hook(event,prompt='old prompt',message='old notification'),(0,None))
        self.assertEqual(board.session(self.conn,'s1'),before)
        self.assertEqual(self.conn.total_changes,changes)
        self.assertEqual(len(messaging.pending(self.conn,'s1')),1)

    def test_old_claude_pretool_denies_without_policy_bypass_or_mail_consumption(self):
        self.switch_to_codex()
        data=self.data(tool_name='Bash',tool_input={'command':'crew doctor'})
        with contextlib.redirect_stderr(io.StringIO()) as error, \
             mock.patch('crew.store.connect',side_effect=AssertionError('must not migrate or write')), \
             mock.patch('crew.guard.check',side_effect=AssertionError('inactive carrier cannot reach policy')):
            self.assertEqual(hooks.handle('guard',data,self.home,self.fake_plugin,{}),(2,None))
        self.assertIn('Codex',error.getvalue())
        self.assertEqual(board.session(self.conn,'s1')['status'],'working')
        self.assertEqual(len(messaging.pending(self.conn,'s1')),1)

    def test_legacy_claude_default_and_switch_back_hooks_are_still_active(self):
        self.assertEqual(self.hook('guard',tool_name='Read',tool_input={'file_path':str(self.root/'x')}),(0,None))
        self.switch_to_codex()
        runtimes.set(self.conn,'s1','claude','s1')
        code,result=self.hook('user-prompt-submit',prompt='current Claude prompt')
        self.assertEqual(code,0)
        self.assertIn('message reserved',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(messaging.pending(self.conn,'s1'),[])

    def test_old_database_without_runtime_table_keeps_claude_default_read_only(self):
        self.conn.execute('DROP TABLE session_runtimes')
        with mock.patch('crew.store.connect',side_effect=AssertionError('guard must not migrate')):
            self.assertEqual(self.hook('guard',tool_name='Read',tool_input={'file_path':str(self.root/'x')}),(0,None))
