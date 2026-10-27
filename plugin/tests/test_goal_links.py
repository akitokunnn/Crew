"""Human results stay with their original goal/person; native sends are controlled fakes."""
import tests
from types import SimpleNamespace
import unittest
from unittest import mock

from crew.execution_host import ExecutionStore, NativeLauncher
from crew.execution_admission import WorkAdmission
from crew import board, chat, codex_client, goal_links, goals, todo
from crew.goal_runtime import Controller
from tests.test_runtimes import RuntimeFixture


class GoalLinkTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.executions = ExecutionStore(self.home)
        self.journals = self.executions.journals
        self.executions.busy = mock.Mock(return_value=False)
        self.controller = Controller(self.executions, WorkAdmission(), mock.Mock(spec=NativeLauncher),
                                      send_input=mock.Mock(return_value={'accepted': True}), stop=mock.Mock(),
                                      interrupt=mock.Mock(), observe_claude=lambda: [])
        self.controller.send = mock.Mock(return_value={'accepted':True,'delivery':'started'})

    def journal(self, execution_id):
        return self.executions.journal(execution_id)

    def target(self, title='原目标', person='member-id'):
        goal = goals.create(self.conn,'demo','chief-id',title,'原始需求',['可复核的 example'])
        if person != 'chief-id':
            goals.assign(self.conn,goal['id'],person,'处理这个目标')
        execution = goals.create_execution(self.conn,goal['id'],person,runtime='claude')
        goals.transition(self.conn,goal['id'],'running')
        return goal,execution

    def decision(self, execution, *, answered=True):
        number = board.ask(self.conn,execution['session_id'],'请选择是否发布',['现在发布','等待验收'])
        goal_links.bind(self.conn,'decision',number,execution['id'])
        if answered:
            board.answer(self.conn,number,'等待验收，保留目前改动')
        return number

    def action(self, execution):
        item = todo.add(self.conn,execution['session_id'],'连接测试设备','在设置里连接设备','状态显示已连接',blocking=True)
        goal_links.bind(self.conn,'todo',item['id'],execution['id'])
        return item

    def deliver(self):
        goal_links.deliver(self.controller,self.conn)

    def assert_no_global_mail(self):
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0],0)

    def test_own_todo_withdrawal_is_not_replayed_as_a_human_answer(self):
        target, execution = self.target()
        item = self.action(execution)
        todo.withdraw(self.conn, execution['session_id'], item['id'], 'Solved by the current AI')
        self.deliver(); self.deliver()
        self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn, 'todo', item['id'])['delivered_version'], 2)
        self.assert_no_global_mail()

    def test_leader_withdrawal_of_a_members_todo_keeps_its_real_ai_origin(self):
        target, execution = self.target()
        item = self.action(execution)
        todo.withdraw(self.conn, 'chief-id', item['id'], 'Use the agreed alternative')
        self.deliver()
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.kwargs['source'], 'crew')
        self.assert_no_global_mail()

    def test_answer_routes_only_to_original_goal_and_person_once(self):
        original,execution = self.target()
        self.target('同一个人的另一个目标')
        self.target('另一成员的目标','other-id')
        number = self.decision(execution)
        self.deliver();self.deliver()
        self.controller.send.assert_called_once()
        args,kwargs = self.controller.send.call_args
        self.assertEqual(args[:2],(original['id'],'member-id'))
        self.assertIn('等待验收，保留目前改动',args[2])
        self.assertEqual(kwargs,{'identity':'human-answer:decision:%s:1'%number,'source':'human'})
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],1)
        self.assertIsNotNone(self.conn.execute('SELECT delivered_at FROM decisions WHERE id=?',(number,)).fetchone()[0])
        self.assert_no_global_mail()

    def test_old_requirement_answer_is_archived_without_waking_any_execution(self):
        target,execution = self.target();number = self.decision(execution)
        goals.amend(self.conn,target['id'],'新增要求，不沿用原排程')
        self.deliver();self.deliver()
        self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],1)
        self.assertIsNone(self.conn.execute('SELECT delivered_at FROM decisions WHERE id=?',(number,)).fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='goal:human-answer-archived'").fetchone()[0],1)
        self.assert_no_global_mail()

    def test_old_round_answer_cannot_wake_a_reopened_goal(self):
        target,execution = self.target();number = self.decision(execution)
        goals.transition(self.conn,target['id'],'cancelled',reason='用户停止')
        reopened = goals.reopen(self.conn,target['id'],'用户明确要求开启后续轮次')
        self.assertEqual(reopened['current_round'],2)
        self.deliver()
        self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],1)
        self.assertEqual(goals.get(self.conn,target['id'])['status'],'queued')

    def test_manual_pause_defers_answer_until_explicit_resume(self):
        target,execution = self.target();number = self.decision(execution)
        goals.request_pause(self.conn,target['id'],manual=True)
        for _ in range(3):self.deliver()
        self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],0)
        self.assertEqual(goals.get(self.conn,target['id'])['status'],'paused')
        goals.resume(self.conn,target['id'],actor=board.HUMAN);self.deliver()
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[:2],(target['id'],'member-id'))

    def test_todo_done_undo_and_next_done_deliver_each_observed_version_once_without_global_notice(self):
        target,execution = self.target();item = self.action(execution)
        self.deliver();self.controller.send.assert_not_called()
        done = todo.resolve(self.conn,'demo',item['id'],'done',note='设备已连接',expected_version=1)
        self.deliver();self.deliver()
        self.assertEqual(self.controller.send.call_count,1)
        self.assertIn('done',self.controller.send.call_args.args[2])
        self.assertEqual(self.controller.send.call_args.kwargs['identity'],'human-answer:todo:%s:2'%item['id'])
        undone = todo.undo(self.conn,'demo',item['id'],done['before'],done['version'])
        self.deliver();self.deliver()
        self.assertEqual(self.controller.send.call_count,2)
        self.assertIn('open',self.controller.send.call_args.args[2])
        self.assertEqual(self.controller.send.call_args.kwargs['identity'],'human-answer:todo:%s:3'%item['id'])
        todo.resolve(self.conn,'demo',item['id'],'done',note='重新确认完成',expected_version=undone['version'])
        self.deliver();self.deliver()
        self.assertEqual(self.controller.send.call_count,3)
        self.assertEqual(goal_links.get(self.conn,'todo',item['id'])['delivered_version'],4)
        self.assertTrue(all(call.args[:2]==(target['id'],'member-id') for call in self.controller.send.call_args_list))
        self.assert_no_global_mail()

    def test_deferred_todo_changes_deliver_latest_user_intent_without_replaying_undone_completion(self):
        target,execution = self.target();item = self.action(execution)
        goals.request_pause(self.conn,target['id'])
        done = todo.resolve(self.conn,'demo',item['id'],'done',expected_version=1)
        todo.undo(self.conn,'demo',item['id'],done['before'],done['version'])
        self.deliver();self.controller.send.assert_not_called()
        goals.resume(self.conn,target['id']);self.deliver()
        self.controller.send.assert_called_once()
        self.assertIn('open',self.controller.send.call_args.args[2])
        self.assertEqual(self.controller.send.call_args.kwargs['identity'],'human-answer:todo:%s:3'%item['id'])
        self.assert_no_global_mail()

    def test_legacy_board_delivery_cannot_consume_goal_bound_decision(self):
        _,execution = self.target();number = self.decision(execution)
        legacy = board.ask(self.conn,'member-id','总对话问题',['A','B']);board.answer(self.conn,legacy,'A')
        delivered = board.deliver(self.conn,'member-id')
        self.assertEqual([item['id'] for item in delivered['answers']],[legacy])
        self.assertIsNone(self.conn.execute('SELECT delivered_at FROM decisions WHERE id=?',(number,)).fetchone()[0])
        self.deliver();self.controller.send.assert_called_once()

    def test_unknown_or_submitted_receipt_in_current_or_old_generation_never_resends(self):
        target,old = self.target();number = self.decision(old)
        goals.set_execution_status(self.conn,old['id'],'idle')
        current = goals.create_execution(self.conn,target['id'],'member-id',runtime='claude')
        identity = 'human-answer:decision:%s:1'%number
        for execution in (current,old):
            for status in ('unknown','submitted'):
                with self.subTest(execution=execution['id'],status=status):
                    self.journal(execution['id']).emit('input_delivery',message_id=identity,status=status,error='RequestTimeout',detail='native receipt timed out')
                    self.deliver();self.deliver()
                    self.controller.send.assert_not_called()
                    self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],0)
        errors = [item for item in self.journal(current['id']).events if item['type']=='error']
        self.assertEqual(len(errors),1)
        self.assertIn('RequestTimeout',errors[0]['text']);self.assertIn('native receipt timed out',errors[0]['text'])

    def test_native_timeout_keeps_answer_pending_without_blocking_other_goal_or_repeating_input(self):
        first,one = self.target();first_id = self.decision(one)
        second,two = self.target('正常目标','other-id');second_id = self.decision(two)
        def send(goal_id,person,text,*,identity,source):
            if goal_id==first['id']:
                self.journal(one['id']).emit('input_delivery',message_id=identity,status='unknown',error='RequestTimeout',detail='turn/start timed out')
                raise codex_client.RequestTimeout('turn/start timed out')
            return {'accepted':True,'delivery':'started'}
        self.controller.send.side_effect=send
        self.deliver();self.assertEqual(self.controller.send.call_count,2)
        self.deliver();self.deliver();self.assertEqual(self.controller.send.call_count,2)
        self.assertEqual(goal_links.get(self.conn,'decision',first_id)['delivered_version'],0)
        self.assertEqual(goal_links.get(self.conn,'decision',second_id)['delivered_version'],1)
        self.assertTrue(any(item['type']=='error' and 'RequestTimeout' in item['text'] and 'turn/start timed out' in item['text'] for item in self.journal(one['id']).events))

    def test_known_acceptance_recovers_database_receipt_without_resuming_waiting_goal_or_sending_again(self):
        target,execution = self.target();number = self.decision(execution)
        identity = 'human-answer:decision:%s:1'%number
        self.journal(execution['id']).emit('input_delivery',message_id=identity,status='accepted',delivery='started')
        goals.transition(self.conn,target['id'],'waiting_user')
        self.deliver();self.deliver()
        self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],1)
        self.assertEqual(goals.get(self.conn,target['id'])['status'],'waiting_user')

    def test_binding_cannot_impersonate_another_person(self):
        _,execution = self.target();number = board.ask(self.conn,'other-id','其他成员的问题',['A','B'])
        with self.assertRaisesRegex(board.BoardError,'提出者不匹配'):
            goal_links.bind(self.conn,'decision',number,execution['id'])

    def test_latest_execution_busy_defers_even_when_original_generation_is_idle(self):
        target,old = self.target();number = self.decision(old)
        goals.set_execution_status(self.conn,old['id'],'idle')
        current = goals.create_execution(self.conn,target['id'],'member-id',runtime='claude')
        self.controller.executions.busy.side_effect = lambda key:key==current['id']
        self.deliver();self.controller.send.assert_not_called()
        self.assertEqual(goal_links.get(self.conn,'decision',number)['delivered_version'],0)
        self.controller.executions.busy.return_value=False;self.controller.executions.busy.side_effect=None
        self.deliver();self.controller.send.assert_called_once()

    def test_unknown_acceptance_does_not_resume_waiting_goal(self):
        target,execution = self.target();number = self.decision(execution)
        identity = 'human-answer:decision:%s:1'%number
        self.journal(execution['id']).emit('input_delivery',message_id=identity,status='unknown',error='TransportError',detail='socket closed')
        goals.transition(self.conn,target['id'],'waiting_user')
        self.deliver();self.controller.send.assert_not_called()
        self.assertEqual(goals.get(self.conn,target['id'])['status'],'waiting_user')


if __name__ == '__main__':unittest.main()
