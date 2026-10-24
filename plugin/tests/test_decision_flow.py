"""Staged disclosure persists without auto-waking old decisions or duplicating requests."""
import tests
from contextlib import closing
import unittest

from crew import board, decision_briefs, decision_flow, store
from tests.test_decision_briefs import brief
from tests.test_runtimes import RuntimeFixture

NOW = '2026-10-25T02:00:00.000Z'


class DecisionFlowTests(RuntimeFixture):
    def ask(self, structured=True):
        return board.ask(self.conn,'member-id','是否现在发布？',
                         **({'brief':brief(),'recommendation':'等待用户验收后再发布'} if structured else {'options':['A. 发版','B. 等待']}))

    def test_new_and_old_pending_default_to_notice_and_reads_never_queue_work(self):
        old, new = self.ask(False), self.ask()
        before = self.conn.total_changes
        for _ in range(3):
            rows = decision_flow.enrich(self.conn,decision_briefs.enrich(self.conn,board.decisions(self.conn,'demo')))
            self.assertEqual([row['presentation_stage'] for row in rows],['notice','notice'])
        self.assertEqual(before,self.conn.total_changes)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        decision_flow.ensure(self.conn)
        self.assertEqual(decision_flow.get(self.conn,old)['presentation_stage'],'notice')
        self.assertEqual(decision_flow.get(self.conn,new)['presentation_stage'],'notice')

    def test_only_explicit_human_discussion_queues_one_explanation_for_the_original_leader(self):
        number = self.ask()
        for actor in ('member-id','chief-id',None,'crew'):
            with self.subTest(actor=actor), self.assertRaisesRegex(board.BoardError,'用户明确'):
                decision_flow.discuss(self.conn,number,'demo',actor=actor)
        first = decision_flow.discuss(self.conn,number,'demo',now=NOW)
        second = decision_flow.discuss(self.conn,number,'demo',now='2026-10-25T02:01:00Z')
        self.assertEqual(first,second)
        self.assertEqual(first['presentation_stage'],'discussion')
        notice = self.conn.execute('SELECT * FROM leader_notices WHERE id=?',(first['notice_id'],)).fetchone()
        self.assertEqual(notice['session_id'],'chief-id')
        self.assertIn('用户点击',notice['text']);self.assertIn('原 ID 不变',notice['text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],1)
        with closing(store.connect(self.home)) as reopened:
            self.assertEqual(decision_flow.get(reopened,number),first)

    def test_ready_requires_prior_discussion_complete_brief_and_explicit_recommendation(self):
        number = self.ask(False)
        with self.assertRaisesRegex(board.Conflict,'先听'):
            decision_flow.ready(self.conn,number,'demo')
        decision_flow.discuss(self.conn,number,'demo',now=NOW)
        with self.assertRaisesRegex(board.Conflict,'不完整'):
            decision_flow.ready(self.conn,number,'demo')
        self.assertEqual(decision_flow.get(self.conn,number)['presentation_stage'],'discussion')
        decision_briefs.revise(self.conn,'chief-id',number,brief=brief(),recommendation='等待用户验收后再发布')
        result = decision_flow.ready(self.conn,number,'demo',now=NOW)
        self.assertEqual(result['presentation_stage'],'ready')
        self.assertEqual(result,decision_flow.ready(self.conn,number,'demo'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:ready'").fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],1)
        with closing(store.connect(self.home)) as reopened:
            self.assertEqual(decision_flow.get(reopened,number)['presentation_stage'],'ready')

    def test_project_scope_answered_and_archived_leader_cannot_request_new_discussion(self):
        number = self.ask()
        with self.assertRaisesRegex(board.BoardError,'工作区'):
            decision_flow.discuss(self.conn,number,'other')
        board.answer(self.conn,number,'等待',board.HUMAN)
        with self.assertRaisesRegex(board.Conflict,'已经结束'):
            decision_flow.discuss(self.conn,number,'demo')
        number = self.ask()
        board.set_status(self.conn,'chief-id','closed')
        with self.assertRaisesRegex(board.Conflict,'leader 已结束'):
            decision_flow.discuss(self.conn,number,'demo')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)

    def test_leader_move_does_not_reroute_an_old_decision_to_the_new_group(self):
        number = self.ask()
        board.join(self.conn,'new-leader','demo','new-chief',self.root,self.root,'main',role='leader')
        self.conn.execute("UPDATE sessions SET group_name='new-chief' WHERE session_id='member-id'")
        result = decision_flow.discuss(self.conn,number,'demo')
        self.assertEqual(result['leader_session_id'],'chief-id')

    def test_missing_recommendation_and_mismatched_options_remain_in_discussion(self):
        number = board.ask(self.conn,'member-id','选择发版时间',brief=brief())
        decision_flow.discuss(self.conn,number,'demo')
        with self.assertRaisesRegex(board.Conflict,'不完整'):
            decision_flow.ready(self.conn,number,'demo')
        self.conn.execute("UPDATE decisions SET recommendation='等待用户验收后再发布', options='[\"冲突动作\",\"其他动作\"]' WHERE id=?",(number,))
        with self.assertRaisesRegex(board.Conflict,'不完整'):
            decision_flow.ready(self.conn,number,'demo')
        self.assertEqual(decision_flow.get(self.conn,number)['presentation_stage'],'discussion')


if __name__ == '__main__':
    unittest.main()
