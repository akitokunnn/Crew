import unittest

from crew import remote_routes


class RemoteRouteTests(unittest.TestCase):
    def test_new_internal_or_unknown_endpoints_are_closed_by_default(self):
        for path in ('/api/admin/chat/send', '/api/connections', '/api/files/open',
                     '/api/files/reveal', '/api/session/native-handoff', '/api/assistant/native-action',
                     '/api/workspace-data/import', '/api/storage/clean', '/api/not-yet-invented'):
            for method in ('GET', 'POST'):
                self.assertFalse(remote_routes.permitted(method, path), (method, path))
        self.assertTrue(remote_routes.permitted('GET', '/app.js', {'/app.js'}))
        self.assertFalse(remote_routes.permitted('GET', '/new.js', {'/app.js'}))

    def test_goal_report_and_version_controls_never_select_an_ai_actor(self):
        for action in ('mode.set', 'report.publish', 'version.capture', 'version.restore', 'versions.list', 'versions.preview'):
            data = {'action': action, 'data': {'goal_id': 'goal'}}
            self.assertEqual('user', remote_routes.human_payload('/api/goals/agent', data)['actor_session_id'])
        for data in ({'action': 'assign'}, {'action': 'mode.set', 'actor_session_id': 'leader'},
                     {'action': 'mode.set', 'execution_id': None},
                     {'action': 'mode.set', 'data': {'actor_session_id': 'leader'}}):
            with self.assertRaises(PermissionError):
                remote_routes.human_payload('/api/goals/agent', data)
        # An execution target is not automatically a claim to be its AI actor.
        target = {'execution_id': 'previous-version', 'path': 'image.png'}
        self.assertEqual(target, remote_routes.human_payload('/api/files/resolve', target))

    def test_login_return_preserves_project_and_fragment_but_never_changes_origin(self):
        target = '/?project=demo#leader=leader-42'
        self.assertEqual(target, remote_routes.safe_next(target))
        for target in ('https://other.test/', '//other.test/', '/%2fother.test/', '/\\other.test/',
                       '/%5cother.test/', '/%0aother.test/', 'javascript:alert(1)',
                       '/remote/login?next=/', '/remote/logout', None):
            self.assertEqual('/', remote_routes.safe_next(target), target)
