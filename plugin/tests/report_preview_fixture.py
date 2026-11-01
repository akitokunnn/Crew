"""Isolated real HTTP/SQLite fixture for the Electron report-preview smoke.

Only model launchers are fake. Reports are new synthetic documents; the fixture
never opens registered user workspaces or calls a real model.
"""
import json
import os
from pathlib import Path
import sys
import traceback

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from crew import codex_tools, goals, runtimes
from test_goal_http import GoalHttpTests


def main():
    case = GoalHttpTests('test_claude_leader_general_and_two_tasks_progress_independently')
    case.setUp()
    try:
        first, second = case.goal(), case.goal()
        goals.assign(case.conn, second['id'], 's1', 'Review this synthetic report.')
        runtimes.set(case.conn, 's1', 'codex', 'report-preview-fixture')
        tools = codex_tools.CodexTools(case.home, 's1',
            goal_handler=case.server.service.chat.goal_controller.agent_action)
        print(json.dumps({'url': case.url, 'home': str(case.home), 'project': str(case.project),
                          'goal_id': first['id'], 'member_goal_id': second['id'],
                          'leader_session_id': 's3', 'member_session_id': 's1'}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            identity = command.get('id')
            try:
                action = command.get('action')
                if action == 'shutdown':
                    break
                if action == 'cli':
                    source = case.project / 'authored-report.html'
                    source.write_text(command['html'], encoding='utf-8')
                    data = {'viewport': command.get('viewport', 'desktop')}
                    code, output, error = case.run_cli(['--session', 's3', 'goal', 'report.preview',
                        first['id'], '--html-file', str(source), '--data', json.dumps(data)])
                    result = {'code': code, 'value': json.loads(output) if output.strip() else None,
                              'error': error}
                elif action == 'codex':
                    result = tools({'tool': 'crew_goal', 'arguments': {'action': 'report.preview',
                        'goal_id': second['id'], 'data': {'document_html': command['html'],
                            'viewport': command.get('viewport', 'mobile')}}})
                elif action == 'inspect':
                    result = {'model_launches': len(case.processes),
                              'goal_status': goals.get(case.conn, first['id'])['status'],
                              'member_goal_status': goals.get(case.conn, second['id'])['status']}
                else:
                    raise ValueError('Unsupported smoke-fixture command')
                print(json.dumps({'id': identity, 'result': result}, ensure_ascii=False), flush=True)
            except Exception:
                print(json.dumps({'id': identity, 'error': traceback.format_exc()}, ensure_ascii=False), flush=True)
    finally:
        case.doCleanups()
        print(json.dumps({'closed': True}), flush=True)


if __name__ == '__main__':
    main()
