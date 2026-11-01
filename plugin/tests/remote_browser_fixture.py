"""Real LAN HTTP/auth and SQLite; only native AI carriers are fake."""
import datetime as dt
import argparse
import json
import os
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tests
from crew import board, decision_flow, todo
from test_goal_http import GoalHttpTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lan-address', default='127.0.0.1')
    args = parser.parse_args()
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    try:
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
        todo.add(case.conn, 's1', '确认手机上的布局', '检查输入框', '可以发送和返回')
        decision = board.ask(case.conn, 's1', '先验证手机布局还是电脑布局？', recommendation='先验证手机', brief={
            'title':'先验证手机还是电脑？', 'context':'两种屏幕都需要核对。', 'why_user':'请决定这次先验哪一边。',
            'recommendation_reason':'先检查较窄的屏幕。', 'impact':'只改变检查顺序。',
            'options':[{'label':'先验证手机','consequence':'先检查窄屏输入与导航。'},
                       {'label':'先验证电脑','consequence':'先检查桌面浏览器。'}]})
        decision_flow.discuss(case.conn, decision, project='demo')
        decision_flow.ready(case.conn, decision, project='demo')
        access = service.remote_access.action({'action':'start', 'bind':args.lan_address, 'port':0})
        print(json.dumps({'url':case.url, 'remote_url':'http://'+args.lan_address+':'+str(access['port']), 'remote_port':access['port'],
                          'leader':'s3', 'goal':goal['id'], 'report':report['id'], 'decision':decision}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'shutdown':
                break
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
        case.doCleanups()
        print(json.dumps({'closed':True}), flush=True)


if __name__ == '__main__':
    main()
