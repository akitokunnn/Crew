"""Real LAN HTTP/auth and SQLite; only native AI carriers are fake."""
import datetime as dt
import argparse
import json
import os
import socket
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tests
from crew import board, decision_flow, git_admin, goal_actions, todo
from test_goal_http import GoalHttpTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lan-address', default='127.0.0.1')
    parser.add_argument('--start-disabled', action='store_true')
    args = parser.parse_args()
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    blocker = None
    try:
        case.git_repo()
        case.git("branch", "-M", "main")
        git_admin.configure(case.conn, "demo", {"revision":git_admin.revision(None), "enabled":True,
            "admin_session_id":"s3", "base":"main", "stable":"", "branch_rules":"隔离测试，只读取提交图。"})
        service = case.server.service
        service.now = lambda: dt.datetime.now(dt.timezone.utc)
        other = case.home / 'other-project'
        other.mkdir()
        board.init_project(case.conn, 'other', other)
        board.join(case.conn, 'remote-other', 'other', '另一个项目', other, other, None, role='leader')
        (case.project / 'remote-note.txt').write_text('远程文件预览证据', encoding='utf-8')
        service.chat.journal('s3').consume({'type':'assistant', 'message':{'id':'remote-file', 'content':[
            {'type':'text', 'text':'文件已准备：[查看文本](remote-note.txt)'}]}})
        goal, report, _, _ = case.awaiting_acceptance()
        progress_goal = case.goal()
        _, progress_receipt, _ = case.send_goal(progress_goal, message_id='browser-stage-fixture')
        progress_data = case.report()
        progress_data.update(outcome='progress', summary='网络页面阶段检查')
        progress_data['acceptance'][0]['criterion_id'] = progress_goal['requirements']['acceptance'][0]['id']
        goal_actions.dispatch(case.home, 's3', None, 'report.publish', {
            'goal_id':progress_goal['id'], 'request_id':'browser-stage-report', 'report':progress_data})
        todo.add(case.conn, 's1', '确认手机上的布局', '检查输入框', '可以发送和返回')
        decision = board.ask(case.conn, 's1', '先验证手机布局还是电脑布局？', recommendation='先验证手机', brief={
            'title':'先验证手机还是电脑？', 'context':'两种屏幕都需要核对。', 'why_user':'请决定这次先验哪一边。',
            'recommendation_reason':'先检查较窄的屏幕。', 'impact':'只改变检查顺序。',
            'options':[{'label':'先验证手机','consequence':'先检查窄屏输入与导航。'},
                       {'label':'先验证电脑','consequence':'先检查桌面浏览器。'}]})
        decision_flow.discuss(case.conn, decision, project='demo')
        decision_flow.ready(case.conn, decision, project='demo')
        access = service.remote_access.action({'action':'start', 'bind':'127.0.0.1' if args.start_disabled else args.lan_address, 'port':0})
        if args.start_disabled:
            service.remote_access.action({'action':'stop'})
        print(json.dumps({'url':case.url, 'remote_url':'http://'+args.lan_address+':'+str(access['port']), 'remote_port':access['port'],
                          'leader':'s3', 'goal':goal['id'], 'report':report['id'], 'decision':decision,
                          'progress_goal':progress_goal['id'], 'progress_execution':progress_receipt['execution_id']}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'shutdown':
                break
            if command['action'] == 'block-address':
                blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                blocker.bind((args.lan_address, access['port']))
                blocker.listen(1)
                print(json.dumps({'blocked':True}), flush=True)
                continue
            if command['action'] == 'unblock-address':
                blocker.close()
                blocker = None
                print(json.dumps({'unblocked':True}), flush=True)
                continue
            if command['action'] == 'change-file':
                (case.project / 'remote-note.txt').write_text(command['text'], encoding='utf-8')
                print(json.dumps({'file_changed':command['id']}), flush=True)
                continue
            if command['action'] == 'finish-turn':
                sid = command['session_id']
                case.finish_turn(sid, service.chat.processes[sid].process)
                print(json.dumps({'turn_finished':sid}), flush=True)
                continue
            if command['action'] == 'reply':
                sid = command['session_id']
                process = service.chat.processes[sid].process
                process.emit({'type':'assistant','message':{'id':command['id'], 'content':[
                    {'type':'text','text':command.get('text','隔离载体已收到这条消息。')}]}})
                case.finish_turn(sid, process)
            elif command['action'] == 'audit':
                sid = command['session_id']
                print(json.dumps({'audit':command['id'], 'messages':[
                    event for event in service.chat.journal(sid).events if event['type']=='message'],
                    'process_count':len(case.processes)}, ensure_ascii=False), flush=True)
    finally:
        if blocker is not None:
            blocker.close()
        case.doCleanups()
        print(json.dumps({'closed':True}), flush=True)


if __name__ == '__main__':
    main()
