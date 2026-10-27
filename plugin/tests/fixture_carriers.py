"""Resolve fake carriers for suites that already inject every model process."""
import shutil
from unittest import mock


def install_lookup(case, root):
    directory = root / 'fake-carriers'
    directory.mkdir()
    for carrier in ('claude', 'codex'):
        (directory / (carrier + '.exe')).touch()
    native_which = shutil.which

    def which(command, *args, **kwargs):
        if command in ('claude', 'codex'):
            return str(directory / (command + '.exe'))
        return native_which(command, *args, **kwargs)

    lookup = mock.patch('shutil.which', side_effect=which)
    lookup.start()
    case.addCleanup(lookup.stop)
