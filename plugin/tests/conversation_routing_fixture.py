"""Private HTTP fixture for native conversation routing smoke; every carrier is fake."""
import datetime as dt
import json
import os
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tests
from test_goal_http import GoalHttpTests
from crew import board, goals, runtimes


def main():
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        source = case.goal()
        _, source_execution, _ = case.send_goal(source, message_id='native-ui-source')
        board.join(case.conn, 'coordinator', 'demo', '协作负责人', case.project, case.project, 'main', role='leader')
        runtimes.set(case.conn, 'coordinator', 'claude')
        work = []
        for index, title in enumerate(('声音评测', '客户端联调')):
            goal = goals.create(case.conn, 'demo', 'coordinator', title, '独立推进，保留原始证据', ['交付可核对证据'])
            code, receipt, _ = case.request('/api/chat/send?project=demo', {
                'session_id': 'coordinator', 'goal_id': goal['id'], 'text': '开始' + title,
                'message_id': 'native-ui-task-' + str(index)})
            assert code == 200, receipt
            work.append((goal['id'], receipt['execution_id']))
        manager = case.server.service.chat
        print(json.dumps({'url': case.url, 'home': str(case.home), 'leader': 'coordinator',
                          'goals': [goal for goal, _ in work]}, ensure_ascii=False), flush=True)
        original = None
        for line in sys.stdin:
            command = line.strip()
            if command == 'request':
                original = manager.send_message('s3', 'coordinator', '请确认接口格式，声音任务继续执行。',
                    execution_id=source_execution['execution_id'], kind='decision', message_id='native-ui-request')
                manager.deliver_messages()
                assert manager.busy('coordinator')
                print(json.dumps({'request': original['message_id']}), flush=True)
            elif command == 'reply':
                case.finish_turn('coordinator', manager.processes['coordinator'].process)
                result = manager.send_message('coordinator', 's3', '接口已核对，通过原消息返回。',
                    kind='result', message_id='native-ui-reply', reply_to=original['message_id'])
                case.finish_turn(source_execution['execution_id'], manager.processes[source_execution['execution_id']].process)
                manager.deliver_messages()
                assert manager.busy(source_execution['execution_id'])
                assert not manager.running('s3')
                assert not manager.busy('coordinator')
                assert all(manager.busy(key) for _, key in work)
                print(json.dumps({'replied': result['message_id'], 'parallel_tasks': len(work),
                                  'source_task_busy': True, 'source_general_started': False}), flush=True)
            elif command == 'shutdown':
                break
    finally:
        case.doCleanups()
        print(json.dumps({'closed': True}), flush=True)


if __name__ == '__main__':
    main()
