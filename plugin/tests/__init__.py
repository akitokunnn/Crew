"""Never let a test connect to the production Crew service."""
import sys
import unittest

violations = []


def deny_production(event, args):
    if event != "socket.connect":
        return
    address = args[1]
    if isinstance(address, tuple) and len(address) >= 2 and address[1] == 47811:
        reason = "测试隔离：禁止连接正式 Crew 服务 127.0.0.1:47811；请使用替身或临时端口 (%r)" % (address,)
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
