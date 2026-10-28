"""Default entrypoints select V2 data without changing explicit HOME routing."""
import tests
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import cli, hooks, store
from crew.storage_service import StorageService
from crew.storage_v2 import __main__ as storage_cli


class ProductIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-product-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('CREW_HOME', None)
        self.home = mock.patch('pathlib.Path.home', return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_store_and_cli_default_to_independent_v2_without_touching_v1(self):
        self.assertEqual(store.default_home(), self.root / '.crew-v2')
        self.assertEqual(cli.parser().parse_args(['status']).home, self.root / '.crew-v2')
        self.assertFalse((self.root / '.crew').exists())
        self.assertFalse((self.root / '.crew-v2').exists())

    def test_cli_and_hook_preserve_explicit_environment_home(self):
        selected = self.root / 'selected-home'
        os.environ['CREW_HOME'] = str(selected)
        self.assertEqual(cli.parser().parse_args(['status']).home, selected)
        with mock.patch('sys.stdin', io.StringIO('{}')), mock.patch('crew.hooks.handle', return_value=(0, None)) as handle:
            self.assertEqual(hooks.main(['session-start']), 0)
        self.assertEqual(handle.call_args.args[2], selected)

    def test_unconfigured_hook_and_storage_cli_use_v2_home(self):
        with mock.patch('sys.stdin', io.StringIO('{}')), mock.patch('crew.hooks.handle', return_value=(0, None)) as handle:
            self.assertEqual(hooks.main(['session-start']), 0)
        self.assertEqual(handle.call_args.args[2], self.root / '.crew-v2')
        repository = mock.Mock()
        repository.list_workspaces.return_value = []
        with mock.patch.object(storage_cli, 'Repository', return_value=repository) as constructor, mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(storage_cli.main(['list']), 0)
        constructor.assert_called_once_with(self.root / '.crew-v2')
        self.assertFalse((self.root / '.crew').exists())

    def test_product_marker_does_not_extend_claude_manifest_schema(self):
        plugin = Path(__file__).resolve().parents[1]
        self.assertEqual(json.loads((plugin / 'crew-product.json').read_text(encoding='utf-8')),
                         {'generation': 2, 'name': 'Crew V2'})
        manifest = json.loads((plugin / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))
        self.assertNotIn('generation', manifest)
        self.assertNotIn('productGeneration', manifest)

    def test_default_v2_preserves_v1_as_discovery_only_and_explicit_homes_stay_isolated(self):
        local = self.root / 'LocalAppData'
        legacy = local / 'Crew-previous'
        legacy.mkdir(parents=True)
        os.environ['LOCALAPPDATA'] = str(local)
        homes = StorageService(self.root / '.crew-v2')._homes()
        self.assertEqual(homes, [self.root / '.crew-v2', self.root / '.crew', legacy])
        selected = self.root / 'explicit'
        self.assertEqual(StorageService(selected)._homes(), [selected])
        self.assertEqual(StorageService(self.root / '.crew-v2', isolated=True)._homes(), [self.root / '.crew-v2'])
        self.assertFalse((self.root / '.crew').exists())


if __name__ == '__main__':
    unittest.main()
