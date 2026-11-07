"""Real local HTTP and journals with controlled fake carriers and recovery time."""
import json
import os
from pathlib import Path
import sys
import threading
from unittest import mock

for name in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(name, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from crew import web
from test_runtime_recovery import RuntimeRecoveryTests
from test_shell import PLUGIN


def main():
    case = RuntimeRecoveryTests('test_only_three_automatic_recoveries_follow_consecutive_crashes')
    case.setUp()
    server = None
    thread = None
    try:
        service = web.Service(case.home, PLUGIN, agents_provider=lambda: [], claude_root=case.claude,
                              codex_root=case.codex, runs_file=case.runs, cwd=case.project,
                              process_factory=case.process_factory, vscode_state_db=case.vscode_state_db,
                              isolated_sources=True)
        service.chat.close()
        service.chat = case.manager
        service.refresh_agents()
        # The fixture advances the same Manager.auto_resume entry point itself.
        # Other production background workers are unnecessary for this browser journey.
        with mock.patch.object(service, 'start'):
            server = web.Server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        scopes = {}
        for runtime in ('claude', 'codex'):
            key, sid, _ = case.start_scope(runtime)
            scopes[runtime] = {'key': key, 'sid': sid, 'native_id': case.native_id(key, sid, None)}
        print(json.dumps({'url': server.url, 'scopes': scopes}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'shutdown':
                break
            scope = scopes[command['runtime']]
            key = scope['key']
            if command['action'] == 'crash':
                case.crash(key)
            case.poll(command['seconds'])
            journal = case.manager.journal(key)
            attempts = [event for event in journal.events
                        if event['type'] == 'runtime_recovery' and event['status'] == 'attempting']
            assert case.native_id(key, scope['sid'], None) == scope['native_id']
            print(json.dumps({'action': command['action'], 'runtime': command['runtime'],
                              'running': case.manager.running(key), 'attempts': len(attempts),
                              'native_id': scope['native_id']}), flush=True)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(5)
            assert not thread.is_alive()
        case.doCleanups()
        print(json.dumps({'closed': True, 'real_model_processes': 0}), flush=True)


if __name__ == '__main__':
    main()
