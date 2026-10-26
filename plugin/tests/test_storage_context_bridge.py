"""The existing context tool reads portable history within its trusted goal scope."""
import json
from unittest import mock

from crew import board, goal_actions, goals
from crew.storage_service import StorageService
from tests.test_goals import GoalFixture


class StorageContextBridgeTests(GoalFixture):
    def test_retrieve_reuses_tool_and_is_bounded_incremental_and_goal_scoped(self):
        goal=goals.create(self.conn,'demo','chief-id','Primary CPU engine','Build primary engine',['Measure output'])
        other=goals.create(self.conn,'demo','chief-id','Other secret goal','Never expose this other request',['Other check'])
        service=StorageService(self.home,isolated=True)
        with mock.patch('subprocess.Popen',side_effect=AssertionError('retrieval started model')):
            service.refresh('demo')
            result=goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id'],'limit_chars':1500})
            self.assertLessEqual(result['used_chars'],1500)
            self.assertNotIn(other['title'],json.dumps(result))
            again=goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id'],'limit_chars':1500,'known_versions':result['versions']})
            self.assertGreaterEqual(again['unchanged_count'],len(result['versions']))

    def test_unmapped_goal_never_falls_back_to_whole_workspace(self):
        goal=self.goal();service=StorageService(self.home,isolated=True);service.refresh('demo')
        with mock.patch('crew.storage_v2.legacy.resolve_goal_id',return_value=None):
            with self.assertRaisesRegex(ValueError,'目标'):
                goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id']})

    def test_unupgraded_history_requires_explicit_open_instead_of_importing_silently(self):
        goal=self.goal()
        with self.assertRaisesRegex(ValueError,'升级'):
            goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id']})

    def test_other_project_payload_cannot_override_bound_actor(self):
        goal=self.goal()
        with self.assertRaises(board.BoardError):
            goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id'],'project':'other'})

    def test_direct_context_without_goal_still_uses_bound_agent(self):
        service=StorageService(self.home,isolated=True);service.refresh('demo')
        result=goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{})
        self.assertLessEqual(result['used_chars'],6000)

    def test_new_goal_after_upgrade_is_indexed_on_demand_without_starting_a_model(self):
        service=StorageService(self.home,isolated=True);service.refresh('demo')
        goal=self.goal()
        with mock.patch('subprocess.Popen',side_effect=AssertionError('read started model')):
            result=goal_actions.dispatch(self.home,'chief-id',None,'context.retrieve',{'goal_id':goal['id']})
        self.assertIn(goal['requirements']['request'],json.dumps(result))
        self.assertIsNotNone(result['history_snapshot_at'])
