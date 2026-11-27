import tests  # Install the production-service connection guard.
import unittest

from crew.ops import executor, registry, scopes
from crew.ops.caller import device
from crew.ops.errors import OpError
from test_shell import PLUGIN, ShellFixture
from crew import goals, remote_routes, web


class RemoteGoalOperationTests(ShellFixture, unittest.TestCase):
    def test_remote_goal_operation_defaults_to_the_authenticated_user(self):
        service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
                              process_factory=self.process_factory, isolated_sources=True)
        self.addCleanup(service.close)
        goal = goals.create(self.conn, 'demo', 's3', 'Version history', 'Read task versions', ['Shown'])
        for kind in ('browser', 'desktop'):
            with self.subTest(kind=kind):
                result = executor.call(service, device({'kind': kind}), 'goal.versions.list',
                                       {'data': {'goal_id': goal['id']}}, project_name='demo')
                self.assertEqual(result['versions'], [])


class RemoteRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operations = registry.operations()

    def test_new_internal_or_unknown_endpoints_are_closed_by_default(self):
        for path in ('/api/admin/chat/send', '/api/connections', '/api/files/open',
                     '/api/files/reveal', '/api/goals/report/folder', '/api/assistant/native-action',
                     '/api/workspace-data/import', '/api/storage/clean', '/api/not-yet-invented'):
            for method in ('GET', 'POST'):
                self.assertFalse(remote_routes.permitted(method, path, (), 'browser'), (method, path))
        self.assertTrue(remote_routes.permitted('GET', '/app.js', {'/app.js'}, 'browser'))
        self.assertFalse(remote_routes.permitted('GET', '/new.js', {'/app.js'}, 'browser'))

    def test_goal_report_and_version_controls_never_select_an_ai_actor(self):
        for kind in ('browser', 'desktop'):
            caller = device({'kind': kind})
            for name in ('goal.mode.set', 'goal.report.publish', 'goal.version.capture', 'goal.version.restore',
                         'goal.versions.list', 'goal.versions.preview'):
                op = self.operations[name]
                with self.subTest(kind=kind, name=name):
                    scopes.authorize(op, caller)
                    scopes.human_payload(op, caller, {'data': {'goal_id': 'goal'}})
                    for extra in ({'actor_session_id': 'leader'}, {'execution_id': None},
                                  {'data': {'actor_session_id': 'leader'}}):
                        with self.assertRaises(OpError):
                            scopes.human_payload(op, caller, extra)
            with self.assertRaises(OpError):
                scopes.authorize(self.operations['goal.assign'], caller)

    def test_remote_file_resolution_accepts_execution_as_target(self):
        caller = device({'kind': 'browser'})
        op = self.operations['files.resolve']
        scopes.authorize(op, caller)
        scopes.human_payload(op, caller, {'execution_id': 'previous-version', 'path': 'image.png'})

    def test_only_desktop_can_supply_native_candidates_and_neither_can_claim_ai_identity(self):
        op = self.operations['leader.create']
        for kind in ('browser', 'desktop'):
            caller = device({'kind': kind})
            for field in ('caller_session_id', 'caller_execution_id', 'actor_session_id'):
                with self.subTest(kind=kind, field=field), self.assertRaises(OpError):
                    scopes.human_payload(op, caller, {field: None})
        for members in ([], [{'session_id': 'native'}]):
            data = {'candidate_members': members}
            scopes.human_payload(op, device({'kind': 'desktop'}), data)
            with self.assertRaises(OpError):
                scopes.human_payload(op, device({'kind': 'browser'}), data)

    def test_login_return_preserves_project_and_fragment_but_never_changes_origin(self):
        target = '/?project=demo#leader=leader-42'
        self.assertEqual(target, remote_routes.safe_next(target))
        for target in ('https://other.test/', '//other.test/', '/%2fother.test/', '/\\other.test/',
                       '/%5cother.test/', '/%0aother.test/', 'javascript:alert(1)',
                       '/remote/login?next=/', '/remote/logout', None):
            self.assertEqual('/', remote_routes.safe_next(target), target)
