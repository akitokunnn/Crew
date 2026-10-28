"""Future member brains, isolated identities and fake spawn plans only."""
import tests
import json
from unittest import mock

from crew import board, member_defaults, models, runtimes, session_ops, settings, spawn
from tests.test_runtimes import RuntimeFixture


class MemberDefaultTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(spawn.codex_client, 'command', return_value=['fixture-codex','app-server'])
        patch.start()
        self.addCleanup(patch.stop)

    def plan(self, **options):
        return spawn.prepare(self.conn, self.home, self.root, 'demo', 'new-member', 'Finite user task', group=options.pop('group','chief'), **options)['spec']

    def test_default_continues_inheriting_parent_without_mutating_existing_sessions(self):
        models.set(self.conn, 'chief-id', 'claude-opus-5-5', 'high')
        before = models.get(self.conn, 'member-id')
        self.assertIsNone(member_defaults.get(self.conn, 'chief-id'))
        result = self.plan()
        self.assertEqual((result['runtime'],result['model'],result['effort']),('claude','claude-opus-5-5','high'))
        self.assertEqual(models.get(self.conn, 'member-id'),before)

    def test_leader_override_wins_over_global_and_clear_restores_global(self):
        global_choice = {'runtime':'codex','model':'global-model','effort':'high'}
        settings.save(self.conn, {**settings.get(self.conn),'member':global_choice})
        self.assertEqual(self.plan()['model'],'global-model')
        own = {'runtime':'codex','model':'group-model','effort':'low'}
        with mock.patch('subprocess.Popen',side_effect=AssertionError('Saving must not launch a model')):
            member_defaults.save(self.conn,'chief-id',own)
        result = self.plan()
        self.assertEqual((result['runtime'],result['model'],result['effort']),('codex','group-model','low'))
        self.assertEqual(member_defaults.for_project(self.conn,'demo'),{'chief-id':own})
        member_defaults.save(self.conn,'chief-id',None)
        self.assertEqual(self.plan()['model'],'global-model')

    def test_explicit_spawn_runtime_clears_other_carrier_model_and_explicit_native_default_is_retained(self):
        member_defaults.save(self.conn,'chief-id',{'runtime':'codex','model':'group-model','effort':'high'})
        choice = self.plan(runtime='claude')
        self.assertEqual((choice['runtime'],choice['model'],choice['effort']),('claude',None,None))
        choice = self.plan(model=None,effort=None)
        self.assertEqual((choice['runtime'],choice['model'],choice['effort']),('codex',None,None))

    def test_rename_keeps_leader_scoped_choice_and_does_not_apply_to_another_group(self):
        own = {'runtime':'codex','model':'group-model','effort':'high'}
        member_defaults.save(self.conn,'chief-id',own)
        session_ops.rename(self.conn,'chief-id','renamed-chief')
        self.assertEqual(self.plan(group='renamed-chief')['model'],'group-model')
        board.join(self.conn,'other-leader','demo','another',self.root,None,None,role='leader')
        self.assertEqual(self.plan(group='another')['runtime'],'claude')

    def test_member_and_invalid_settings_cannot_be_saved_and_legacy_json_stays_readable(self):
        with self.assertRaises(board.BoardError):
            member_defaults.save(self.conn,'member-id',{'runtime':'codex','model':None,'effort':None})
        with self.assertRaises(settings.SettingsError):
            member_defaults.save(self.conn,'chief-id',{'runtime':'wrong','model':None,'effort':None})
        self.assertIsNone(member_defaults.get(self.conn,'chief-id'))
        settings.save(self.conn,{**settings.get(self.conn),'member':{'runtime':'codex','model':'member-model','effort':'high'}})
        legacy = json.loads(self.conn.execute('SELECT ai_json FROM ai_settings').fetchone()[0])
        self.assertEqual(set(legacy),{'default','digest','git','operations'})
        self.assertEqual(settings.get(self.home)['member']['model'],'member-model')
