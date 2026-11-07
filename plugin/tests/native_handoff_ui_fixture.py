"""Real HTTP/SQLite for handoff errors; only the Codex carrier is replaced."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
from unittest import mock

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_goal_http import GoalHttpTests
from test_codex_runtime import FakeClient
from crew import codex_client, native_handoff, runtimes


def main():
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    native = '11111111-1111-4111-8111-111111111111'
    state = {'archived': True, 'calls': []}
    class Client(FakeClient):
        def resume_thread(self, identity, **params):
            state['calls'].append('thread/resume')
            if state['archived']:
                raise codex_client.RpcError('thread/resume', {'code': -32600, 'message': 'session is archived'})
            return super().resume_thread(identity, **params)
        def request(self, method, params, **options):
            state['calls'].append(method)
            if method == 'thread/turns/list':
                return {'data': [], 'nextCursor': None}
            return super().request(method, params, **options)
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        runtimes.set(case.conn, 's3', 'codex', native)
        native_handoff.remember(case.conn, 's3', native, case.home / 'native')
        case.conn.execute("UPDATE native_session_controls SET state='external',last_error='previous active writer conflict' WHERE session_id='s3'")
        with mock.patch('crew.codex_runtime._ObservedClient', Client):
            print(json.dumps({'url': case.url, 'leader': 's3', 'native_id': native}), flush=True)
            for line in sys.stdin:
                if line.strip() == 'allow':
                    state['archived'] = False
                    print(json.dumps({'allowed': True}), flush=True)
                elif line.strip() == 'shutdown':
                    print(json.dumps({'native_calls': state['calls']}), flush=True)
                    break
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
