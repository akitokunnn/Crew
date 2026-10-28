"""Goal-only QQ delivery with real temporary SQLite and the existing fake transport."""
import tests
import datetime as dt
import contextlib
import json
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from crew import board, chat, goal_links, goal_runtime, goals, notifications, qq, session_ops, store, todo
from tests.test_goals import GoalFixture
from tests.test_notifications import FakeQQ

NOW='2026-10-25T01:00:00.000Z'


class GoalNotificationTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.clock=store.parse_iso(NOW);self.locked=False;self.client=FakeQQ();self.journals={}
        (self.home/'qq.json').write_text(json.dumps({'app_id':'fake-app','app_secret':'fake-secret'}),encoding='utf-8')
        (self.home/'notify.json').write_text(json.dumps({'owner_openid':'owner','since':NOW,'manual_away':False,
            'current_leader_session_id':'chief-id','leader_numbers':{}}),encoding='utf-8')
        manager=SimpleNamespace(home=self.home,path=lambda sid:self.home/'chat'/(sid+'.jsonl'),journal=self.journal,
                                busy=mock.Mock(return_value=False), work=lambda **kwargs: contextlib.nullcontext(True))
        self.controller=goal_runtime.Controller(manager)
        self.controller.send=mock.Mock(return_value={'accepted':True,'delivery':'started'})
        self.service=SimpleNamespace(home=self.home,now=lambda:self.clock,chat=manager,write_lock=threading.RLock(),
                                     state=lambda project:{'attention':[]})
        self.inbox=self.new_inbox()

    def journal(self, sid):
        if sid not in self.journals:self.journals[sid]=chat.Journal(self.home/'chat'/(sid+'.jsonl'))
        return self.journals[sid]

    def new_inbox(self):
        result=notifications.Inbox(self.service,self.client,lock_status=lambda:self.locked)
        self.addCleanup(result.close)
        return result

    def stamp(self):return self.clock.isoformat(timespec='milliseconds').replace('+00:00','Z')

    def finish(self, outcome='completed', *, finalized=True):
        target=self.goal(now=self.stamp());goals.transition(self.conn,target['id'],'running',now=self.stamp())
        report=goals.save_report(self.conn,target['id'],self.report(outcome),now=self.stamp())
        goals.request_finish(self.conn,target['id'],report['id'],now=self.stamp())
        if finalized:self.assertTrue(goals.finalize_finish(self.conn,target['id'],now=self.stamp())['finalized'])
        return target,report

    def push(self):
        self.inbox.set_away(True);self.inbox.tick()

    def test_finalized_html_pushes_once_only_while_away_and_survives_restart(self):
        target,report=self.finish()
        self.inbox.tick();self.assertEqual(self.client.sent,[])
        self.push();self.assertEqual(len(self.client.sent),1)
        text=self.client.sent[0]['text']
        for part in ('目标交付',target['title'],'第 1 轮','已达标','Measured the speaker handover.','完整 HTML 报告','Crew'):
            self.assertIn(part,text)
        self.assertNotIn('localhost',text);self.assertNotIn('127.0.0.1',text);self.assertNotIn('/api/goals/report',text)
        key='goal-report:'+report['id']
        record=self.conn.execute('SELECT * FROM notification_messages WHERE item_id=?',(key,)).fetchone()
        self.assertEqual((record['kind'],record['status']),('goal_report','sent'))
        self.assertEqual(json.loads(record['content'])['item']['goal_id'],target['id'])
        self.inbox.tick();self.clock+=dt.timedelta(seconds=35);self.inbox.tick()
        self.inbox.close();self.inbox=self.new_inbox();self.inbox.tick()
        self.assertEqual(len(self.client.sent),1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages WHERE item_id=?',(key,)).fetchone()[0],1)

    def test_pending_finish_or_paused_goal_does_not_push_a_report(self):
        target,report=self.finish(finalized=False)
        self.push();self.assertEqual(self.client.sent,[])
        goals.request_pause(self.conn,target['id'],now=self.stamp())
        result=goals.finalize_finish(self.conn,target['id'],now=self.stamp())
        self.assertFalse(result['finalized'])
        self.clock+=dt.timedelta(seconds=35);self.inbox.tick()
        self.assertEqual(self.client.sent,[])
        self.assertIsNone(self.conn.execute('SELECT 1 FROM notification_messages WHERE item_id=?',('goal-report:'+report['id'],)).fetchone())

    def test_pause_between_pending_and_flush_is_rechecked_before_sending(self):
        target,report=self.finish('awaiting_acceptance')
        self.locked=True;self.inbox.tick()
        self.assertIn('goal-report:'+report['id'],self.inbox.pending)
        goals.request_pause(self.conn,target['id'],now=self.stamp())
        self.clock+=dt.timedelta(seconds=35);self.inbox.flush()
        self.assertEqual(self.client.sent,[])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages').fetchone()[0],0)

    def test_only_the_finalized_report_is_announced_not_drafts_member_progress_or_todos(self):
        target=self.goal(now=self.stamp());goals.transition(self.conn,target['id'],'running',now=self.stamp())
        draft_data=self.report();draft_data['summary']='中间尝试，没有最终结论'
        draft=goals.save_report(self.conn,target['id'],draft_data,now=self.stamp())
        board.report(self.conn,'member-id','成员内部流水，不应推送',now=self.stamp())
        todo.add(self.conn,'member-id','接入设备','在设置里连接','状态已连接',now=self.stamp())
        self.push();self.assertEqual(self.client.sent,[])
        self.clock+=dt.timedelta(seconds=1)
        report=goals.save_report(self.conn,target['id'],self.report(),now=self.stamp())
        goals.request_finish(self.conn,target['id'],report['id'],now=self.stamp())
        goals.finalize_finish(self.conn,target['id'],now=self.stamp())
        self.push();self.assertEqual(len(self.client.sent),1)
        self.assertNotIn('中间尝试',self.client.sent[0]['text']);self.assertNotIn('成员内部流水',self.client.sent[0]['text'])
        self.assertIsNone(self.conn.execute('SELECT 1 FROM notification_messages WHERE item_id=?',('goal-report:'+draft['id'],)).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notification_messages WHERE kind='goal_report'").fetchone()[0],1)

    def test_failed_and_waiting_acceptance_reports_keep_their_real_outcome(self):
        for outcome,label in (('failed','未达标'),('awaiting_acceptance','待用户验收')):
            with self.subTest(outcome=outcome):
                self.finish(outcome);self.push()
                self.assertIn('结论：'+label,self.client.sent[-1]['text'])
                self.assertNotIn('已达标',self.client.sent[-1]['text'])

    def test_old_finalized_report_before_binding_window_is_not_pushed(self):
        self.clock-=dt.timedelta(minutes=1);self.finish();self.clock+=dt.timedelta(minutes=1)
        self.push();self.assertEqual(self.client.sent,[])

    def test_failed_transport_retains_real_receipt_and_does_not_blindly_replay_after_restart(self):
        _,report=self.finish()
        self.client.send_errors.append(qq.QQError('proactive delivery unavailable',kind='network'))
        self.inbox.set_away(True)
        with self.assertRaisesRegex(qq.QQError,'proactive delivery unavailable'):self.inbox.tick()
        row=self.conn.execute('SELECT * FROM notification_messages WHERE item_id=?',('goal-report:'+report['id'],)).fetchone()
        self.assertEqual(row['status'],'error');self.assertIn('QQError(network)',row['error']);self.assertIn('proactive delivery unavailable',row['error'])
        self.inbox.close();self.inbox=self.new_inbox();self.inbox.tick()
        self.assertIn('proactive delivery unavailable',self.inbox.status()['error'])
        self.assertEqual(len(self.client.operations),1);self.assertEqual(self.client.sent,[])

    def test_scoped_qq_answer_saves_for_original_goal_without_a_second_general_leader_notice(self):
        target=self.goal(now=self.stamp());goals.assign(self.conn,target['id'],'member-id','实施目标')
        execution=goals.create_execution(self.conn,target['id'],'member-id',runtime='claude')
        goals.transition(self.conn,target['id'],'running')
        number=board.ask(self.conn,'member-id','是否可以发布',['现在发布','等待验收'])
        goal_links.bind(self.conn,'decision',number,execution['id'])
        decision=next(item for item in board.decisions(self.conn,'demo') if item['id']==number)
        self.inbox.save_answer(self.conn,decision,'等待验收','qq-answer')
        self.assertIn('已保存，将回原目标',self.client.sent[-1]['text']);self.assertNotIn('已转告 leader',self.client.sent[-1]['text'])
        row=self.conn.execute('SELECT status,answer,answered_by FROM decisions WHERE id=?',(number,)).fetchone()
        self.assertEqual(tuple(row),('answered','等待验收','qq'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        self.controller.send.assert_not_called()
        goal_links.deliver(self.controller,self.conn);goal_links.deliver(self.controller,self.conn)
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[:2],(target['id'],'member-id'))

    def test_unscoped_qq_answer_keeps_existing_leader_notification(self):
        number=board.ask(self.conn,'member-id','旧会话问题',['A','B'])
        decision=next(item for item in board.decisions(self.conn,'demo') if item['id']==number)
        self.inbox.save_answer(self.conn,decision,'A','legacy-qq-answer')
        notice=self.conn.execute('SELECT * FROM leader_notices').fetchone()
        self.assertEqual((notice['session_id'],notice['source']),('chief-id','qq'))
        self.assertIn('已转告 leader',self.client.sent[-1]['text'])

    def test_local_preview_addresses_are_not_sent_to_the_phone(self):
        target=self.goal(now=self.stamp());goals.transition(self.conn,target['id'],'running')
        data=self.report();data['summary']='验证完成。预览 http://localhost:8616/report 与 http://127.0.0.1:8616/report'
        report=goals.save_report(self.conn,target['id'],data,now=self.stamp())
        goals.request_finish(self.conn,target['id'],report['id'],now=self.stamp());goals.finalize_finish(self.conn,target['id'],now=self.stamp())
        self.push();text=self.client.sent[-1]['text']
        self.assertIn('验证完成',text);self.assertIn('本机地址请回 Crew 查看',text)
        self.assertNotIn('localhost',text);self.assertNotIn('127.0.0.1',text)

    def test_final_briefs_keep_individual_report_receipts_and_stay_short(self):
        reports=[]
        for _ in range(3):
            _,report=self.finish();reports.append(report['id'])
        self.push();self.assertEqual(len(self.client.sent),3)
        for report_id in reports:
            row=self.conn.execute('SELECT status,message_id FROM notification_messages WHERE item_id=?',('goal-report:'+report_id,)).fetchone()
            self.assertEqual(row['status'],'sent')
        self.assertEqual(len({item['result']['id'] for item in self.client.sent}),3)
        self.assertTrue(all(len(item['text'])<notifications.TEXT_LIMIT for item in self.client.sent))

    def test_archived_leader_after_batching_suppresses_final_report_at_flush(self):
        _,report=self.finish();self.locked=True;self.inbox.tick()
        self.assertIn('goal-report:'+report['id'],self.inbox.pending)
        session_ops.archive(self.conn,'chief-id',now=self.stamp())
        self.inbox.flush();self.inbox.tick()
        self.assertEqual(self.client.sent,[])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages').fetchone()[0],0)

    def test_closed_group_is_not_announced_even_when_its_leader_row_is_still_open(self):
        self.finish()
        self.conn.execute("UPDATE groups SET closed_at=? WHERE project='demo' AND leader_session_id='chief-id'",(self.stamp(),))
        self.push();self.assertEqual(self.client.sent,[])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages').fetchone()[0],0)


if __name__=='__main__':unittest.main()
