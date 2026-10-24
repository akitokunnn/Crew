"""Native Git with closed input and output handles safe for Windows timeouts."""
from shutil import which
import subprocess
import sys
import tempfile
from pathlib import Path


def executable():
    selected=which('git')
    if selected is None:return 'git'
    path=Path(selected)
    if sys.platform=='win32' and path.name.lower()=='git.exe' and path.parent.name.lower() in ('cmd','bin'):
        for architecture in ('mingw64','mingw32'):
            native=path.parent.parent/architecture/'bin'/'git.exe'
            if native.is_file():return str(native)
    return str(path)


def run(arguments,timeout=10,*,command_executable=None):
    """Return text CompletedProcess; preserve OSError/TimeoutExpired unchanged.

    Read probes default to ten seconds. A caller performing a repository write
    must explicitly pass timeout=None so a checkout is never killed mid-write.
    """
    command=[executable() if command_executable is None else command_executable,*arguments]
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    # communicate(PIPE) may wait for a wrapper's surviving descendant after
    # killing the timed-out immediate process. File handles never wait for EOF.
    with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
        result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=timeout,**options)
        stdout.seek(0);stderr.seek(0)
        return subprocess.CompletedProcess(command,result.returncode,stdout.read().decode('utf-8',errors='replace'),stderr.read().decode('utf-8',errors='replace'))
