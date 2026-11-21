"""Never let a test connect to the production Crew service."""
import os
import sys
import tempfile
import unittest

# macOS's default temp dir is under /var, a symlink to /private/var. Crew refuses symlinked HOME paths and
# resolves real paths, so tests would see two spellings of one directory. CI already uses a resolved TMPDIR;
# this does the same for local runs, including the processes tests start.
if sys.platform == 'darwin':
    tempfile.tempdir = os.environ['TMPDIR'] = os.path.realpath(tempfile.gettempdir())
# Git localizes its errors by the system language (Chinese on some Macs); tests compare Git's English text.
os.environ['LC_MESSAGES'] = 'C'

violations = []


def deny_production(event, args):
    if event != "socket.connect":
        return
    address = args[1]
    if isinstance(address, tuple) and len(address) >= 2 and address[1] in (47811, 7122):
        reason = "测试隔离：禁止连接正式 Crew 服务端口 47811/7122；请使用替身或临时端口 (%r)" % (address,)
        violations.append(reason)
        raise AssertionError(reason)


sys.addaudithook(deny_production)
_original_stop_test = unittest.TestResult.stopTest


def stop_test(self, test):
    # Thread exceptions must also fail the owning test instead of only printing a traceback.
    while violations:
        reason = violations.pop(0)
        self.addFailure(test, (AssertionError, AssertionError(reason), None))
    return _original_stop_test(self, test)


unittest.TestResult.stopTest = stop_test
