"""Registration contracts for the HTTP route tables."""
import tests
import unittest

from crew import report_preview, web
from crew.web_api import routes


DIRECT = {'/api/admin/update/prepare', '/api/admin/update/cancel', '/api/admin/connection/prepare'}
AGENT_PREVIEW = '/api/goals/agent'

# Concrete operations accepted by each prefix handler, independent of the HTTP whitelist.
PREFIX_ACTIONS = {
    '/api/workspace-data/': ('discover', 'open', 'export', 'preview', 'import', 'handoff', 'activate'),
    '/api/storage/': ('preview', 'clean', 'restore', 'trash/empty', 'policy'),
    '/api/maintenance/': ('department/preview', 'department/compact', 'cleanup/preview', 'cleanup'),
    '/api/goals/': ('create', 'action', 'context', 'agent'),
    '/api/session/': ('appearance', 'rename', 'archive', 'delete', 'restore', 'undo', 'stop', 'options', 'compact'),
}


def prefix_paths(table):
    return {prefix + action for prefix in table for action in PREFIX_ACTIONS[prefix]}


class RouteTableTests(unittest.TestCase):
    def test_post_paths_match_all_registered_operations(self):
        self.assertEqual(routes.POST_A_PREFIX.keys() | routes.POST_C_PREFIX.keys(), set(PREFIX_ACTIONS))
        registered = set().union(DIRECT, report_preview.WORKER_PATHS, {AGENT_PREVIEW}, routes.POST_A,
                                 prefix_paths(routes.POST_A_PREFIX), routes.POST_B_EARLY, routes.POST_B_LATE,
                                 routes.POST_C, prefix_paths(routes.POST_C_PREFIX))
        self.assertEqual(web.POST_PATHS, registered)

    def test_duplicate_keys_and_stage_ownership(self):
        with self.assertRaisesRegex(ValueError, 'duplicate-path'):
            routes._table([('/duplicate-path', object()), ('/duplicate-path', object())])

        stage_a = [*report_preview.WORKER_PATHS, AGENT_PREVIEW, *routes.POST_A, *prefix_paths(routes.POST_A_PREFIX)]
        stage_b = [*routes.POST_B_EARLY, *routes.POST_B_LATE]
        stage_c = [*routes.POST_C, *prefix_paths(routes.POST_C_PREFIX)]
        registrations = [*DIRECT, *stage_a, *stage_b, *stage_c]
        self.assertIn(AGENT_PREVIEW, prefix_paths(routes.POST_C_PREFIX))
        self.assertCountEqual(registrations, [*set(registrations), AGENT_PREVIEW])

    def test_get_routes_do_not_shadow_static_files(self):
        self.assertEqual(routes.GET.keys() & web.STATIC.keys(), set())
