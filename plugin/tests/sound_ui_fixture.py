"""Isolated HTTP/SQLite fixture. Commands drive fake carriers through native adapters."""
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
import test_goal_http as goal_http
from test_codex_runtime import FakeClient
from crew import board, goal_reports, personal, runtimes


class IndependentClient(FakeClient):
    sequence = 0

    def __init__(self, **options):
        super().__init__(**options)
        type(self).sequence += 1
        self.thread_id = 'sound-native-' + str(self.sequence)


def main():
    case = goal_http.GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    patcher = mock.patch('crew.codex_runtime._ObservedClient', IndependentClient)
    patcher.start()
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        assistant = personal.ensure_assistant(case.conn, case.home)['session_id']
        board.join(case.conn, 'sound-codex', 'demo', 'Codex 负责人', case.project, case.project, None, role='leader')
        runtimes.set(case.conn, 'sound-codex', 'codex')
        task = case.goal()
        scopes = {'leader': ('s3', None), 'codex': ('sound-codex', None), 'task': ('s3', task['id']), 'assistant': (assistant, None)}
        active, sequence = {}, 0
        print(json.dumps({'url': case.url, 'home': str(case.home), 'assistant': assistant, 'leader': 's3', 'goal': task['id']}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'shutdown':
                break
            scope = command.get('scope', 'leader')
            sid, goal = scopes[scope]
            manager = case.server.service.chat
            if command['action'] in ('stream', 'complete', 'tools'):
                sequence += 1
                body = {'session_id': sid, 'text': '核对本轮声音 ' + str(sequence), 'message_id': 'sound-' + str(sequence)}
                if goal:
                    body['goal_id'] = goal
                code, receipt, _ = case.request('/api/chat/send' + ('' if scope == 'assistant' else '?project=demo'), body)
                assert code == 200, receipt
                identity = receipt.get('execution_id', sid)
                active[scope] = identity
                current = manager.processes[identity]
                if scope == 'codex':
                    current.client.emit('item/agentMessage/delta', turnId=current.client.turn_id,
                                        itemId='sound-answer-' + str(sequence), delta='这一轮结论已经核对。')
                else:
                    content = ([{'type': 'tool_use', 'id': 'read-' + str(sequence), 'name': 'Read', 'input': {}}]
                               if command['action'] == 'tools' else [{'type': 'text', 'text': '这一轮结论已经核对。'}])
                    current.process.emit({'type': 'assistant', 'message': {'id': 'sound-answer-' + str(sequence), 'content': content}})
            if command['action'] in ('finish', 'complete', 'tools'):
                identity = active[scope]
                current = manager.processes[identity]
                if scope == 'codex':
                    current.client.finish(command.get('status', 'completed'))
                elif command.get('status', 'completed') == 'completed':
                    case.finish_turn(identity, current.process)
                else:
                    current.process.emit({'type': 'result', 'is_error': True, 'errors': ['隔离测试中的失败']})
                journal = manager.journal(identity)
                with journal.condition:
                    assert journal.condition.wait_for(lambda: journal.status == 'idle', timeout=3)
            if command['action'] == 'stage':
                report = case.report()
                report['outcome'] = 'progress'
                goal_reports.publish(case.conn, task['id'], report, request_id='sound-stage', actor=board.HUMAN)
            if command['action'] == 'delivery':
                delivered, report, _, _ = case.awaiting_acceptance()
                command.update(goal=delivered['id'], report=report['id'])
            print(json.dumps({'done': command}), flush=True)
    finally:
        case.doCleanups()
        patcher.stop()


if __name__ == '__main__':
    main()
