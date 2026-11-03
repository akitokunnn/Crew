"""Isolated real HTTP histories for the multi-workspace Electron smoke test."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(key, None)
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]

from crew import board, web
from test_assistant_http import AssistantHttpTests
from test_codex_runtime import FakeClient


class IndependentClient(FakeClient):
    counter = 0

    def __init__(self, **options):
        super().__init__(**options)
        type(self).counter += 1
        self.thread_id = 'chat-smoke-native-' + str(type(self).counter)


def main():
    case = AssistantHttpTests('test_explicit_message_uses_shared_chat_delivery_and_keeps_identity')
    case.setUp()
    patcher = mock.patch('crew.codex_runtime._ObservedClient', IndependentClient)
    patcher.start()
    try:
        manager = case.server.service.chat
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        old_ref = sys.argv[1] if len(sys.argv) > 1 else None
        if old_ref:
            old_chat = subprocess.check_output(
                ['git', 'show', old_ref + ':plugin/crew/web/chat.js'], cwd=Path(__file__).resolve().parents[2])

            class OldAssets(web.Handler):
                def do_GET(self):
                    if self.path.split('?', 1)[0] == '/chat.js':
                        self._send(200, old_chat, 'text/javascript; charset=utf-8')
                        return
                    super().do_GET()

            case.server.RequestHandlerClass = OldAssets

        def request(path, data):
            code, value, _ = case.request(path, data)
            assert code == 200, (path, code, value)
            return value

        def finish(key):
            current = manager.processes[key]
            if current.runtime == 'codex':
                current.client.finish()
            else:
                case.finish_turn(key, current.process)

        def seed(key, marker):
            journal = manager.journal(key)
            for index in range(24):
                journal.message(marker + '-question-' + str(index), '隔离对话问题 ' + str(index))
                journal.emit('assistant', id=marker + '-answer-' + str(index), content=[{
                    'type': 'text', 'text': marker + ' · ' + str(index) + '\n\n' +
                    '这段历史用于验证阅读位置、消息补齐和草稿保存。' * 9}])
                journal.emit('turn_result', status='completed', success=True)
            journal.set_status('idle')

        projects = []
        for index in range(8):
            name = 'chat-smoke-' + str(index)
            directory = case.root / name
            directory.mkdir()
            board.init_project(case.conn, name, directory)
            runtime = 'claude' if index % 2 == 0 else 'codex'
            person = request('/api/leader?project=' + name, {
                'name': '对话验收 ' + str(index), 'task': '隔离假载体测试', 'runtime': runtime})
            sid = person['session_id']
            finish(sid)
            marker = '工作区历史 ' + str(index)
            seed(sid, marker)
            projects.append({'name': name, 'root': str(directory), 'session_id': sid,
                             'runtime': runtime, 'marker': marker})

        first = projects[0]
        member = request('/api/spawn?project=' + first['name'], {
            'name': '成员验收', 'task': '隔离成员对话', 'leader_session_id': first['session_id']})
        finish(member['session_id'])
        seed(member['session_id'], '成员历史')
        goal = request('/api/goals/create?project=' + first['name'], {
            'leader_session_id': first['session_id'], 'title': '隔离任务', 'request': '检查消息恢复',
            'acceptance': ['恢复消息且不重复']})['goal']
        receipt = request('/api/chat/send?project=' + first['name'], {
            'session_id': first['session_id'], 'goal_id': goal['id'], 'text': '隔离任务输入'})
        finish(receipt['execution_id'])
        seed(receipt['execution_id'], '任务历史')
        request('/api/chat/send?project=' + first['name'], {
            'session_id': first['session_id'], 'goal_id': goal['id'], 'text': '验收期间继续运行此隔离任务'})
        assistant = case.open_assistant()
        request('/api/chat/send', {'session_id': assistant, 'text': '隔离助手输入'})
        finish(assistant)
        seed(assistant, '助手历史')
        print(json.dumps({'url': case.url, 'home': str(case.home), 'projects': projects,
                          'member': member['session_id'], 'goal': goal['id'], 'assistant': assistant,
                          'model_launches': len(case.processes) + IndependentClient.counter}), flush=True)

        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'shutdown':
                break
            if command['action'] == 'inspect':
                print(json.dumps({'request_id': command['request_id'],
                                  'task_alive': manager.running(receipt['execution_id']),
                                  'task_busy': manager.busy(receipt['execution_id']),
                                  'model_launches': len(case.processes) + IndependentClient.counter}), flush=True)
            if command['action'] == 'append':
                journal = manager.journal(command['session_id'])
                event = journal.emit('assistant', id=command['id'], content=[{
                    'type': 'text', 'text': command['text']}])
                journal.emit('turn_result', status='completed', success=True)
                print(json.dumps({'request_id': command['request_id'], 'seq': event['seq']}), flush=True)
            if command['action'] == 'compact':
                for identity in command['session_ids']:
                    manager.journal(identity).emit('compact', pre_tokens=8000, post_tokens=2000)
                print(json.dumps({'request_id': command['request_id']}), flush=True)
    finally:
        case.doCleanups()
        patcher.stop()


if __name__ == '__main__':
    main()
