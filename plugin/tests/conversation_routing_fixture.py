"""Private HTTP fixture for native conversation routing smoke; every carrier is fake."""
import datetime as dt
import json
import os
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(key, None)
plugin_root = Path(os.environ.get('CREW_TEST_PLUGIN_ROOT', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Test helpers are not shipped.
sys.path.insert(0, str(plugin_root))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tests
from test_goal_http import GoalHttpTests
from crew import board, goal_actions, goal_work, goals, runtimes, todo, web
import test_goal_http
import test_shell

assert Path(web.__file__).resolve().is_relative_to(plugin_root), web.__file__

# The same fixture can validate the built backend without reading source assets.
test_goal_http.PLUGIN = test_shell.PLUGIN = plugin_root


def main():
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        source = case.goal()
        _, source_execution, _ = case.send_goal(source, message_id='native-ui-source')
        board.join(case.conn, 'coordinator', 'demo', '协作负责人', case.project, case.project, 'main', role='leader')
        runtimes.set(case.conn, 'coordinator', 'claude')
        board.roster(case.conn, 'demo', 'move', '协作负责人', ['one'])
        todo.add(case.conn, 's1', '确认录音测试环境', '打开测试页面', '结果可核对')
        work = []
        for index, title in enumerate(('声音评测', '客户端联调')):
            goal = goals.create(case.conn, 'demo', 'coordinator', title, '独立推进，保留原始证据', ['交付可核对证据'])
            code, receipt, _ = case.request('/api/chat/send?project=demo', {
                'session_id': 'coordinator', 'goal_id': goal['id'], 'text': '开始' + title,
                'message_id': 'native-ui-task-' + str(index)})
            assert code == 200, receipt
            work.append((goal['id'], receipt['execution_id']))
            item = goal_work.add(case.conn, goal['id'], 'verify', '验证流式分段边界' if index == 0 else '完成界面联调',
                '整段流结束后400ms内返回 A、B 及重叠片段' if index == 0 else '核对实际交互结果', actor='coordinator')
            goal_work.update(case.conn, goal['id'], item['key'], item['version'], status='blocked',
                             note='隔离演练：等待测试数据，尚未证明效果达标', actor='coordinator')
            if index == 0:
                report = case.report()
                report.update(outcome='progress', summary='阶段方案待真实实验验证', document_html='<h1>旧版阶段正文</h1>')
                report['acceptance'][0]['criterion_id'] = goal['requirements']['acceptance'][0]['id']
                report['acceptance'][0]['status'] = 'unverified'
                goal_actions.dispatch(case.home, 'coordinator', None, 'report.publish',
                    {'goal_id': goal['id'], 'request_id': 'routing-stage', 'report': report})
        manager = case.server.service.chat
        delivered = None
        if os.environ.get('CREW_UI_REVIEW_FIXTURE') == '1':
            from crew.codex_runtime import process_identity
            for index in range(8):
                stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=8-index)
                active = index >= 5
                case.conn.execute('INSERT INTO codex_runs(run_id,crew_session_id,task_file,workdir,started_at,runtime,pid,pid_created) VALUES(?,?,?,?,?,?,?,?)',
                    ('ui-dispatch-'+str(index), 'coordinator', '审查协作记录 ' + '长标题与原始说明。' * 12,
                     str(case.project), stamp.isoformat(), 'claude' if index % 2 else 'codex',
                     os.getpid() if active else None, process_identity(os.getpid()) if active else None))
            code, reply, _ = case.request('/api/chat/send?project=demo', {'session_id': 'coordinator',
                'text': '准备总会话，独立任务继续推进。', 'message_id': 'ui-general-ready'})
            assert code == 200, reply
            case.finish_turn('coordinator', manager.processes['coordinator'].process)
            delivered_goal, delivered_report, _, _ = case.awaiting_acceptance()
            code, accepted, _ = case.request('/api/goals/action?project=demo', {
                'goal_id': delivered_goal['id'], 'action': 'accept',
                'version': delivered_goal['version'], 'report_id': delivered_report['id']})
            assert code == 200, accepted
            delivered = {'goal_id': delivered_goal['id'], 'leader': 's3', 'report_id': delivered_report['id']}
        print(json.dumps({'url': case.url, 'home': str(case.home), 'leader': 'coordinator',
                          'goals': [goal for goal, _ in work], 'plugin_root': str(plugin_root),
                          'web_module': web.__file__, 'delivered': delivered}, ensure_ascii=False), flush=True)
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
            elif command.startswith('finish-task:'):
                target = command.split(':', 1)[1]
                task = goals.get(case.conn, target)
                execution = manager.goal_controller.current(case.conn, target, task['leader_session_id'])
                case.finish_turn(execution['id'], manager.processes[execution['id']].process)
                manager.goal_controller.sync(case.conn, target)
                print(json.dumps({'task_finished': target}), flush=True)
            elif command.startswith('compact-finish:'):
                target = command.split(':', 1)[1]
                key = 'coordinator' if target == 'general' else next(execution for goal, execution in work if goal == target)
                current = manager.processes[key]
                if current.pending_compact:
                    case.finish_turn(key, current.process)
                current.process.emit({'type': 'system', 'subtype': 'compact_boundary',
                                      'compact_metadata': {'pre_tokens': 50000, 'post_tokens': 12000 if target == 'general' else None}})
                current.process.emit({'type': 'result', 'local_command': 'compact'})
                print(json.dumps({'compact_finished': target}), flush=True)
    finally:
        case.doCleanups()
        print(json.dumps({'closed': True}), flush=True)


if __name__ == '__main__':
    main()
