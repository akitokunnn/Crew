"""Serve an isolated real Crew UI for either carrier using test-only processes."""
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from crew import board
from test_claude_subagents import ClaudeChildHttpTests, call, result, task
from test_codex_subagents import NativeChildHttpTests, collab


def main(runtime):
    if runtime == 'claude':
        case = ClaudeChildHttpTests('test_leader_and_member_general_children_have_their_own_task_rows_without_roster_growth')
        case.setUp()
        board.roster(case.conn, 'demo', 'remove', 'lead', ['one'])
        _, running, native = case.start()
        url, sid = case.url, 's3'
    else:
        case = NativeChildHttpTests('test_leader_has_three_child_task_rows_without_registering_members_and_keeps_updates_after_parent_turn')
        case.setUp()
        case.plugin = Path(__file__).resolve().parents[1]
        case.leader()
        running, client = case.make_running()
        running.send('核对登录、界面和结果', 'initial')
        server, _ = case.start_http(running)
        url, sid = 'http://127.0.0.1:' + str(server.server_port), 'worker'

    def start(index, name):
        if runtime == 'claude':
            identity = 'call-' + str(index)
            record = call(identity, name='Task' if index == 2 else 'Agent', background=True)
            record['message']['content'][0]['input']['description'] = name
            case.emit(running, native, record, 'running')
            launched = result(identity, status='async_launched')
            launched['tool_use_result']['agentId'] = 'agent-' + str(index)
            case.emit(running, native, launched)
        else:
            client.emit('item/completed', item={'id': 'start-' + str(index), 'type': 'subAgentActivity',
                'kind': 'started', 'agentThreadId': 'child-' + str(index), 'agentPath': '/root/' + name})

    try:
        for index, name in enumerate(['检查登录', '验证界面', '核对结果'], 1):
            start(index, name)
        print(json.dumps({'url': url, 'sid': sid, 'home': str(case.home), 'runtime': runtime}), flush=True)
        for line in sys.stdin:
            command = line.strip()
            if command == 'finish':
                for index, status in enumerate(['completed', 'failed', 'interrupted'], 1):
                    if runtime == 'claude':
                        notification = task('task_notification', task_id='agent-' + str(index), tool_use_id='call-' + str(index),
                            status='stopped' if status == 'interrupted' else status, summary='隔离验收：未找到预期控件' if status == 'failed' else '')
                        case.emit(running, native, notification)
                    else:
                        client.emit('turn/completed', threadId='child-' + str(index),
                            turn={'id': 'turn-' + str(index), 'status': status,
                                  'error': {'message': '隔离验收：未找到预期控件'} if status == 'failed' else None})
                if runtime == 'claude':
                    case.emit(running, native, {'type': 'result', 'is_error': False})
                else:
                    client.finish()
                print(json.dumps({'finished': True}), flush=True)
            elif command == 'disconnect':
                start(4, '等待断线确认')
                running.process.finish() if runtime == 'claude' else running.close()
                assert running.reader_done.wait(3)
                print(json.dumps({'disconnected': True}), flush=True)
            elif command == 'shutdown':
                break
    finally:
        case.doCleanups()
        print(json.dumps({'closed': True}), flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
