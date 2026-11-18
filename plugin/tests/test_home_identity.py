"""Default entrypoints select ~/.crew without changing explicit HOME routing."""
import tests
import io
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
        temporary = tempfile.TemporaryDirectory(prefix='crew-home-identity-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('CREW_HOME', None)
        self.home = mock.patch('pathlib.Path.home', return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)

    def test_store_and_cli_default_to_crew_home_without_creating_it(self):
        self.assertEqual(store.default_home(), self.root / '.crew')
        self.assertEqual(cli.parser().parse_args(['status']).home, self.root / '.crew')
        self.assertFalse((self.root / '.crew').exists())

    def test_cli_and_hook_preserve_explicit_environment_home(self):
        selected = self.root / 'selected-home'
        os.environ['CREW_HOME'] = str(selected)
        self.assertEqual(cli.parser().parse_args(['status']).home, selected)
        with mock.patch('sys.stdin', io.StringIO('{}')), mock.patch('crew.hooks.handle', return_value=(0, None)) as handle:
            self.assertEqual(hooks.main(['session-start']), 0)
        self.assertEqual(handle.call_args.args[2], selected)

    def test_unconfigured_hook_and_storage_cli_use_default_home(self):
        with mock.patch('sys.stdin', io.StringIO('{}')), mock.patch('crew.hooks.handle', return_value=(0, None)) as handle:
            self.assertEqual(hooks.main(['session-start']), 0)
        self.assertEqual(handle.call_args.args[2], self.root / '.crew')
        repository = mock.Mock()
        repository.list_workspaces.return_value = []
        with mock.patch.object(storage_cli, 'Repository', return_value=repository) as constructor, mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(storage_cli.main(['list']), 0)
        constructor.assert_called_once_with(self.root / '.crew')


if __name__ == '__main__':
    unittest.main()
