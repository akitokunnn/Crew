"""Confirmed configuration changes, separate histories and native acceptance."""
import tests
from contextlib import closing
import json
import sqlite3
import threading
import unittest
from unittest import mock

from crew import board,chat,goal_context,goals,models,runtimes,runtime_switch,store
from tests.test_runtimes import RuntimeFixture
from tests.test_chat import FakeProcess

PLUGIN=__import__('pathlib').Path(__file__).resolve().parents[1]
CATALOG=[{'id':'claude','available':True},{'id':'codex','available':True,'models':[{'id':'gpt-test','efforts':['low','high']}]}]
NOW='2026-10-24T00:00:00.000Z'


class RuntimeSwitchTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.factory=mock.Mock(side_effect=FakeProcess)
        self.manager=chat.Manager(self.home,PLUGIN,lambda:[],process_factory=self.factory)
        self.addCleanup(self.manager.close)
        resolver=mock.patch('crew.chat.claude_executable',return_value='test-claude')
        resolver.start();self.addCleanup(resolver.stop)
        board.set_card(self.conn,'member-id','responsibility','observable proof')

    def prepare(self,runtime='codex',**options):
        return self.manager.prepare_runtime(self.conn,'member-id',runtime,CATALOG,now=NOW,**options)

    def switch(self,preview,**options):
        return self.manager.switch_runtime(self.conn,'member-id',preview['preview_id'],True,CATALOG,now=NOW,**options)

    def launch_claude(self):
        self.manager._launch(board.session(self.conn,'member-id'),{'agent':None,'id':'test-spawn'},resume=True)
        return self.manager.processes['member-id']

    def test_prepare_and_switch_have_no_native_process_or_model_turn_and_preserve_crew(self):
        before=board.session(self.conn,'member-id');card=board.card(self.conn,'member-id')
        preview=self.prepare(model='gpt-test',effort='low')
        self.assertFalse(preview['starts_turn']);self.assertGreater(preview['handoff']['chars'],0)
        self.assertIn('observable proof',preview['handoff']['text'])
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        result=self.switch(preview)
        self.assertEqual(result['status'],'configured');self.assertFalse(result['starts_turn'])
        self.factory.assert_not_called()
        after=board.session(self.conn,'member-id')
        for field in ('session_id','crew_name','group_name','task','role','cwd','branch','worktree'):
            self.assertEqual(after[field],before[field])
        self.assertEqual(board.card(self.conn,'member-id'),card)
        self.assertTrue(runtime_switch.pending(self.conn,'member-id'))

    def test_confirmation_expiration_staleness_and_idempotent_success(self):
        preview=self.prepare()
        with self.assertRaises(board.BoardError):self.manager.switch_runtime(self.conn,'member-id',preview['preview_id'],False,CATALOG,now=NOW)
        with self.assertRaisesRegex(board.Conflict,'过期'):
            self.manager.switch_runtime(self.conn,'member-id',preview['preview_id'],True,CATALOG,now='2026-10-24T00:06:00Z')
        self.manager.journal('member-id').message('changed','new user input')
        with self.assertRaisesRegex(board.Conflict,'状态已经变化'):self.switch(preview)
        preview=self.prepare();result=self.switch(preview)
        self.assertEqual(self.manager.switch_runtime(self.conn,'member-id',preview['preview_id'],True,[],now='2026-10-25T00:00:00Z'),result)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='session:runtime-switch'").fetchone()[0],1)

    def test_leader_and_member_can_switch_only_when_proven_idle_and_local(self):
        leader_preview=self.manager.prepare_runtime(self.conn,'chief-id','codex',CATALOG)
        self.assertFalse(leader_preview['starts_turn'])
        self.manager.switch_runtime(self.conn,'chief-id',leader_preview['preview_id'],True,CATALOG)
        back=self.manager.prepare_runtime(self.conn,'chief-id','claude',CATALOG)
        self.manager.switch_runtime(self.conn,'chief-id',back['preview_id'],True,CATALOG)
        self.assertEqual(runtimes.get(self.conn,'chief-id')['runtime'],'claude')
        board.set_status(self.conn,'member-id','working')
        with self.assertRaisesRegex(board.Conflict,'正在工作'):self.prepare()
        board.set_status(self.conn,'member-id','idle')
        self.manager.agents_provider=lambda:[{'sessionId':'member-id','pid':123,'name':'external'}]
        with self.assertRaisesRegex(board.Conflict,'外部会话'):self.prepare()
        self.factory.assert_not_called()
        self.manager.agents_provider=mock.Mock(side_effect=OSError('unknown process list'))
        with self.assertRaisesRegex(board.Conflict,'状态未知'):self.prepare()

    def test_target_options_use_target_catalog_and_never_inherit_other_runtime(self):
        models.set(self.conn,'member-id','claude-opus-5-5','max')
        for options in ({'model':'claude-opus-5-5'},{'model':'gpt-test','effort':'max'},{'effort':'high'}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.prepare(**options)
        preview=self.prepare();self.assertEqual((preview['to']['model'],preview['to']['effort']),(None,None))
        self.switch(preview)
        self.assertEqual(models.get(self.conn,'member-id'),{'model':None,'effort':None})
        runtimes.set(self.conn,'member-id','codex','native-codex')
        models.set(self.conn,'member-id','gpt-test','high')
        back=self.prepare('claude',model='claude-sonnet-5-5',effort='low');self.switch(back)
        self.assertEqual(runtimes.get(self.conn,'member-id'),{'runtime':'claude','native_id':'member-id'})
        again=self.prepare('codex');self.assertTrue(again['to']['resume_existing']);self.switch(again)
        self.assertEqual(runtimes.get(self.conn,'member-id')['native_id'],'native-codex')
        old=runtime_switch.binding(self.conn,'member-id','claude')
        self.assertEqual((old['model'],old['effort']),('claude-sonnet-5-5','low'))

    def test_first_codex_to_claude_creates_then_switch_back_resumes_original_histories(self):
        runtimes.set(self.conn,'member-id','codex','native-existing')
        self.switch(self.prepare('claude'))
        self.assertIsNone(runtimes.get(self.conn,'member-id')['native_id'])
        current=self.launch_claude()
        self.assertIn('--session-id',current.process.command);self.assertNotIn('--resume',current.process.command)
        signal=threading.Event();real_set=runtimes.set
        def remembered(*args,**kwargs):
            result=real_set(*args,**kwargs);signal.set();return result
        with mock.patch('crew.chat.runtimes.set',side_effect=remembered):
            current.process.emit({'type':'system','subtype':'init','session_id':'member-id'})
            self.assertTrue(signal.wait(3))
        self.switch(self.prepare('codex'))
        self.assertEqual(runtimes.get(self.conn,'member-id')['native_id'],'native-existing')
        self.switch(self.prepare('claude'))
        current=self.launch_claude()
        self.assertIn('--resume',current.process.command);self.assertNotIn('--session-id',current.process.command)

    def test_handoff_persists_failed_start_and_write_and_consumes_only_native_user_ack(self):
        runtimes.set(self.conn,'member-id','codex','native-existing')
        preview=self.prepare('claude',handoff='REVIEWED HANDOFF');self.switch(preview)
        with mock.patch('crew.chat.claude_executable',side_effect=OSError('target missing')):
            with self.assertRaises(OSError):self.launch_claude()
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        self.assertIn('target missing',runtime_switch.pending(self.conn,'member-id')['last_error'])
        current=self.launch_claude()
        self.manager._send('member-id','plain user input',identity='accepted-message')
        self.assertIsNotNone(runtime_switch.pending(self.conn,'member-id'))
        native=json.loads(current.process.stdin.getvalue().splitlines()[-1])
        self.assertIn('REVIEWED HANDOFF',native['message']['content'])
        self.assertEqual([e['text'] for e in current.journal.events if e['type']=='message'],['plain user input'])
        signal=threading.Event();real_accepted=runtime_switch.accepted
        def accepted(*args,**kwargs):
            value=real_accepted(*args,**kwargs);signal.set();return value
        with mock.patch('crew.chat.runtime_switch.accepted',side_effect=accepted):
            current.process.emit({**native,'isReplay':True})
            self.assertTrue(signal.wait(3))
        self.assertIsNone(runtime_switch.pending(self.conn,'member-id'))
        self.assertFalse(runtime_switch.accepted(self.conn,'member-id','accepted-message',preview['preview_id']))

    def test_configuration_failure_rolls_back_and_pending_acceptance_is_bound_to_message_and_runtime(self):
        preview=self.prepare()
        with mock.patch('crew.runtime_switch.models.set',side_effect=ValueError('cannot store options')):
            with self.assertRaises(ValueError):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        self.assertIsNone(runtime_switch.pending(self.conn,'member-id'))
        self.switch(preview)
        text,identifier=runtime_switch.input_text(self.conn,'member-id','next','m1')
        self.assertIn('受信交接',text)
        self.assertFalse(runtime_switch.accepted(self.conn,'member-id','wrong',identifier))
        runtime_switch.failed(self.conn,'member-id','rpc refused')
        with closing(store.connect(self.home)) as reopened:
            self.assertIsNotNone(runtime_switch.pending(reopened,'member-id'))
            self.assertTrue(runtime_switch.accepted(reopened,'member-id','m1',identifier))

    def test_database_busy_during_ack_keeps_reader_and_handoff_alive_until_next_input(self):
        runtimes.set(self.conn,'member-id','codex','native-existing')
        self.switch(self.prepare('claude'))
        current=self.launch_claude()
        self.manager._send('member-id','first input',identity='first-ack')
        native=json.loads(current.process.stdin.getvalue().splitlines()[-1])
        with mock.patch('crew.chat.runtime_switch.accepted',side_effect=sqlite3.OperationalError('database is locked')):
            current.process.emit(native)
            current.process.emit({'type':'assistant','session_id':'member-id','message':{'id':'stream-after-busy','content':[{'type':'text','text':'still streaming'}]}})
            current.process.emit({'type':'result','is_error':False})
            with current.journal.condition:
                self.assertTrue(current.journal.condition.wait_for(lambda:current.journal.status=='idle',timeout=3))
        self.assertFalse(current.reader_done.is_set())
        self.assertIsNotNone(runtime_switch.pending(self.conn,'member-id'))
        self.assertTrue(any('确认记录失败' in event.get('text','') for event in current.journal.events))
        self.assertTrue(any(event['type']=='assistant' for event in current.journal.events))
        self.manager._send('member-id','retry input',identity='second-ack')
        native=json.loads(current.process.stdin.getvalue().splitlines()[-1])
        signal=threading.Event();original=runtime_switch.accepted
        def accepted(*args,**kwargs):
            result=original(*args,**kwargs);signal.set();return result
        with mock.patch('crew.chat.runtime_switch.accepted',side_effect=accepted):
            current.process.emit(native)
            self.assertTrue(signal.wait(3))
        self.assertIsNone(runtime_switch.pending(self.conn,'member-id'))

    def test_assistant_stream_confirms_native_binding_once_without_opening_database_per_message(self):
        current=self.launch_claude()
        original=store.connect
        with mock.patch('crew.chat.store.connect',side_effect=original) as connect:
            for index in range(3):
                current.process.emit({'type':'assistant','session_id':'member-id','message':{'id':'stream-'+str(index),'content':[{'type':'text','text':str(index)}]}})
            current.process.emit({'type':'result','is_error':False})
            with current.journal.condition:
                self.assertTrue(current.journal.condition.wait_for(lambda:current.journal.status=='idle',timeout=3))
            self.assertEqual(connect.call_count,1)

    def test_commit_rechecks_busy_catalog_and_external_and_target_start_checks_old_external(self):
        preview=self.prepare()
        with self.assertRaises(board.Conflict):
            self.manager.switch_runtime(self.conn,'member-id',preview['preview_id'],True,[{'id':'codex','available':False}],now=NOW)
        self.manager.agents_provider=lambda:[{'sessionId':'member-id','pid':456,'name':'external'}]
        with self.assertRaisesRegex(board.Conflict,'外部会话'):self.switch(preview)
        self.manager.agents_provider=lambda:[];self.switch(preview)
        self.manager.agents_provider=lambda:[{'sessionId':'member-id','pid':456,'name':'external'}]
        with self.assertRaisesRegex(board.Conflict,'外部会话'):
            self.manager._check_offline(board.session(self.conn,'member-id'))

    def test_idle_owned_shutdown_failure_keeps_old_configuration_and_unconfigured_receipt(self):
        current=chat.Running(FakeProcess([]),self.manager.journal('member-id'))
        current.reader=mock.Mock()
        current.journal.set_status('idle');self.manager.executions.register('member-id', current)
        preview=self.prepare()
        with mock.patch.object(self.manager,'detach',side_effect=board.BoardError('drain timed out')):
            with self.assertRaisesRegex(board.BoardError,'drain timed out'):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        self.assertEqual(self.conn.execute('SELECT status FROM runtime_switches WHERE id=?',(preview['preview_id'],)).fetchone()[0],'preview')
        self.assertIsNone(runtime_switch.pending(self.conn,'member-id'))
        current.process.finish();current.reader_done.set()

    def test_metadata_changed_while_draining_cannot_be_overwritten_and_inactive_native_stays_reserved(self):
        current=chat.Running(FakeProcess([]),self.manager.journal('member-id'))
        current.reader=mock.Mock()
        current.journal.set_status('idle');self.manager.executions.register('member-id', current)
        preview=self.prepare()
        def detach(_):
            current.process.finish();current.reader_done.set()
            models.set(self.conn,'member-id','claude-sonnet-5-5','low')
        with mock.patch.object(self.manager,'detach',side_effect=detach):
            with self.assertRaisesRegex(board.Conflict,'停止过程中'):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        self.switch(self.prepare())
        runtimes.set(self.conn,'member-id','codex','reserved-native')
        self.switch(self.prepare('claude'))
        with self.assertRaises(board.Conflict):runtimes.set(self.conn,'other-id','codex','reserved-native')

    def test_a_close_callback_without_native_exit_cannot_publish_a_new_runtime(self):
        runtimes.set(self.conn,'member-id','codex','still-running-native')
        current=chat.Running(FakeProcess([]),self.manager.journal('member-id'))
        current.runtime='codex';current.reader=mock.Mock();current.close=mock.Mock()
        current.journal.set_status('idle');self.manager.executions.register('member-id', current)
        preview=self.prepare('claude')
        with mock.patch.object(self.manager,'detach',return_value=None):
            with self.assertRaisesRegex(board.Conflict,'尚未真正退出'):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'codex')
        self.assertIsNone(runtime_switch.pending(self.conn,'member-id'))
        current.process.finish();current.reader_done.set()

    def test_owned_idle_overrides_stale_working_attendance_but_busy_or_unowned_is_rejected(self):
        current=chat.Running(FakeProcess([]),self.manager.journal('member-id'))
        current.reader=mock.Mock();current.journal.set_status('idle');self.manager.executions.register('member-id', current)
        board.set_status(self.conn,'member-id','working')
        self.assertTrue(runtime_switch.state(self.conn,board.session(self.conn,'member-id'),self.manager)['allowed'])
        preview=self.prepare()
        current.journal.set_status('working')
        self.assertFalse(runtime_switch.state(self.conn,board.session(self.conn,'member-id'),self.manager)['allowed'])
        with self.assertRaisesRegex(board.Conflict,'正在工作'):self.switch(preview)
        current.journal.set_status('idle')
        preview=self.prepare()
        def detached(_):current.process.finish();current.reader_done.set()
        with mock.patch.object(self.manager,'detach',side_effect=detached):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'codex')
        self.assertEqual(board.session(self.conn,'member-id')['status'],'offline')
        board.set_status(self.conn,'member-id','working')
        self.assertFalse(runtime_switch.state(self.conn,board.session(self.conn,'member-id'),self.manager)['allowed'])
        with self.assertRaisesRegex(board.Conflict,'正在工作'):self.prepare('claude')

    def test_owned_pid_lookup_survives_process_retiring_during_the_poll(self):
        process=FakeProcess([]);current=chat.Running(process,self.manager.journal('member-id'))
        self.manager.executions.register('member-id', current)
        polling=threading.Event();retired=threading.Event()
        def poll():
            polling.set()
            if not retired.wait(3):raise AssertionError('retirement did not run')
            return None
        def retire():
            if polling.wait(3):self.manager.executions.remove('member-id')
            retired.set()
        worker=threading.Thread(target=retire);worker.start()
        try:
            with mock.patch.object(process,'poll',side_effect=poll):
                self.assertFalse(self.manager.owns_pid('member-id',process.pid))
        finally:worker.join(3);process.finish()
        self.assertFalse(worker.is_alive())

    def goal_execution(self, sid='member-id', *, title='验收目标'):
        target=goals.create(self.conn,'demo','chief-id',title,'实现需求',['实际 example 达标'])
        if sid!='chief-id':goals.assign(self.conn,target['id'],sid,'完成可验证工作')
        execution=goals.create_execution(self.conn,target['id'],sid,runtime='claude')
        return target,execution

    def owned_execution(self, execution, *, busy=False):
        current=chat.Running(FakeProcess([]),self.manager.journal(execution['id']))
        current.execution=execution;current.reader=mock.Mock();current.stderr_reader=mock.Mock()
        current.journal.set_status('working' if busy else 'idle')
        self.manager.executions.register(execution['id'], current)
        self.addCleanup(current.reader_done.set);self.addCleanup(current.process.finish)
        return current

    def test_running_goal_blocks_leader_and_assigned_member_even_before_native_launch(self):
        target=goals.create(self.conn,'demo','chief-id','待处理目标','需求',['example'])
        goals.assign(self.conn,target['id'],'member-id','执行')
        goals.transition(self.conn,target['id'],'running')
        for sid in ('chief-id','member-id'):
            with self.subTest(sid=sid),self.assertRaisesRegex(board.Conflict,'先暂停'):
                self.manager.prepare_runtime(self.conn,sid,'codex',CATALOG)
            self.assertFalse(runtime_switch.state(self.conn,board.session(self.conn,sid),self.manager)['allowed'])
        # A different unassigned person is not constrained by this goal.
        self.manager.prepare_runtime(self.conn,'other-id','codex',CATALOG)
        self.factory.assert_not_called()

    def test_paused_goal_with_an_actual_busy_execution_still_blocks_switch(self):
        target,execution=self.goal_execution();goals.request_pause(self.conn,target['id'])
        current=self.owned_execution(execution,busy=True)
        with self.assertRaisesRegex(board.Conflict,'仍在工作'):
            self.prepare()
        self.assertIsNone(current.process.poll())
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')

    def test_switch_drains_and_configures_only_this_person_paused_executions_preserving_native_histories(self):
        target,execution=self.goal_execution()
        goals.bind_native(self.conn,execution['id'],'claude','scoped-claude')
        goals.bind_native(self.conn,execution['id'],'codex','scoped-codex')
        prepared=goal_context.build(self.conn,execution['id'],base='必要规则',latest_input='开始')
        goal_context.mark_applied(self.conn,prepared['manifest']['id'],execution_id=execution['id'])
        goals.request_pause(self.conn,target['id'])
        current=self.owned_execution(execution)
        other_goal,other_execution=self.goal_execution('other-id',title='其他人的目标')
        goals.transition(self.conn,other_goal['id'],'running')
        other_current=self.owned_execution(other_execution,busy=True)
        preview=self.prepare(model='gpt-test',effort='low')
        self.assertEqual([item['id'] for item in preview['scope']['executions']],[execution['id']])
        self.assertIn('本目标上下文',preview['cost']['notice'])
        detached=[]
        def detach(key):
            detached.append(key);self.manager.processes[key].process.finish();self.manager.processes[key].reader_done.set()
        with mock.patch.object(self.manager,'detach',side_effect=detach):self.switch(preview)
        self.assertEqual(detached,[execution['id']]);self.assertIsNone(other_current.process.poll())
        configured=goals.get_execution(self.conn,execution['id'])
        self.assertEqual((configured['runtime'],configured['model'],configured['effort']),('codex','gpt-test','low'))
        self.assertEqual({row['runtime']:row['native_id'] for row in configured['bindings']},{'claude':'scoped-claude','codex':'scoped-codex'})
        self.assertEqual(goals.get_execution(self.conn,other_execution['id'])['runtime'],'claude')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_applied WHERE execution_id=?',(execution['id'],)).fetchone()[0],0)
        self.assertTrue(goal_context.manifest(self.conn,prepared['manifest']['id'])['applied'])
        self.factory.assert_not_called()

    def test_goal_resume_or_a_new_binding_invalidates_preview_before_any_process_is_drained(self):
        target,execution=self.goal_execution();goals.request_pause(self.conn,target['id'])
        current=self.owned_execution(execution);preview=self.prepare()
        goals.bind_native(self.conn,execution['id'],'codex','new-native-after-preview')
        with mock.patch.object(self.manager,'detach') as detach,self.assertRaisesRegex(board.Conflict,'状态已经变化'):
            self.switch(preview)
        detach.assert_not_called();self.assertIsNone(current.process.poll())
        preview=self.prepare();goals.resume(self.conn,target['id'])
        with mock.patch.object(self.manager,'detach') as detach,self.assertRaisesRegex(board.Conflict,'状态已经变化'):
            self.switch(preview)
        detach.assert_not_called()

    def test_external_native_goal_writer_and_unfinished_reader_cannot_be_hidden_by_idle_identity(self):
        target,execution=self.goal_execution();goals.request_pause(self.conn,target['id'])
        goals.bind_native(self.conn,execution['id'],'claude','external-native')
        self.manager.agents_provider=lambda:[{'sessionId':'external-native','pid':123}]
        self.assertFalse(runtime_switch.state(self.conn,board.session(self.conn,'member-id'),self.manager,agents=self.manager.agents_provider())['allowed'])
        with self.assertRaisesRegex(board.Conflict,'外部会话'):self.prepare()
        self.manager.agents_provider=lambda:[]
        current=self.owned_execution(execution);current.process.finish()
        with self.assertRaisesRegex(board.Conflict,'输出还没有排空'):self.prepare()

    def test_goal_configuration_failure_rolls_back_person_configuration_and_preview(self):
        target,execution=self.goal_execution();goals.request_pause(self.conn,target['id'])
        preview=self.prepare()
        with mock.patch('crew.runtime_switch.goals.configure_execution',side_effect=board.Conflict('scope changed')):
            with self.assertRaisesRegex(board.Conflict,'scope changed'):self.switch(preview)
        self.assertEqual(runtimes.get(self.conn,'member-id')['runtime'],'claude')
        self.assertEqual(goals.get_execution(self.conn,execution['id'])['runtime'],'claude')
        self.assertEqual(self.conn.execute('SELECT status FROM runtime_switches WHERE id=?',(preview['preview_id'],)).fetchone()[0],'preview')


if __name__=='__main__':unittest.main()
