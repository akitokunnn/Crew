"""Enforce dependency direction; behavior remains covered through real interfaces."""
import tests
import ast
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'crew'


def dependencies():
    graph = {}
    for file in ROOT.rglob('*.py'):
        name = 'crew.' + '.'.join(file.relative_to(ROOT).with_suffix('').parts)
        if file.name == '__init__.py':
            name = name.removesuffix('.__init__')
        package = name if file.name == '__init__.py' else name.rsplit('.', 1)[0]
        imports = set()
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom):
                base = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
                imports.update(base + '.' + item.name if node.module is None else base for item in node.names)
            elif isinstance(node, ast.Import):
                imports.update(item.name for item in node.names)
        graph[name] = imports
    return graph


def route_to(graph, start, forbidden):
    queue, seen = [(start, [start])], set()
    while queue:
        item, path = queue.pop()
        if item in seen:
            continue
        seen.add(item)
        if item != start and item in forbidden:
            return path
        queue.extend((dependency, path + [dependency]) for dependency in graph.get(item, ()) if dependency in graph)
    return None


class ModuleArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = dependencies()

    def test_domain_and_transport_primitives_do_not_reach_application_entrypoints(self):
        modules = (
            'board', 'store', 'goals', 'goal_context', 'messaging', 'message_receipts',
            'session_journal', 'claude_events', 'native_process', 'execution_admission',
            'web_assets', 'codex_events', 'codex_client', 'execution_host',
            'workspace_comments', 'attention', 'decision_actions',
            'workspace_snapshot', 'notice_delivery', 'session_library', 'session_presence',
        )
        forbidden = {'crew.web', 'crew.cli', 'crew.chat'}
        for module in modules:
            with self.subTest(module=module):
                name = 'crew.' + module
                self.assertIn(name, self.graph)
                path = route_to(self.graph, name, forbidden)
                self.assertIsNone(path, 'Lower-level module reaches composition root: ' + ' -> '.join(path or ()))

    def test_normalized_history_does_not_depend_on_a_native_adapter(self):
        forbidden = {'crew.claude_events', 'crew.native_process', 'crew.codex_runtime', 'crew.codex_client'}
        path = route_to(self.graph, 'crew.session_journal', forbidden)
        self.assertIsNone(path, 'Journal owns normalized history, not native interpretation: ' + ' -> '.join(path or ()))

    def test_feature_modules_do_not_import_the_http_composition_root(self):
        modules = [name for name in self.graph if name.startswith('crew.web_api.')]
        self.assertTrue(modules)
        for module in modules:
            with self.subTest(module=module):
                path = route_to(self.graph, module, {'crew.web', 'crew.cli'})
                self.assertIsNone(path, 'Feature imports the application that composes it: ' + ' -> '.join(path or ()))

    def test_goal_execution_does_not_reverse_import_the_chat_manager(self):
        path = route_to(self.graph, 'crew.goal_runtime', {'crew.chat', 'crew.web', 'crew.cli'})
        self.assertIsNone(path, 'Goal execution must use runtime capabilities: ' + ' -> '.join(path or ()))
