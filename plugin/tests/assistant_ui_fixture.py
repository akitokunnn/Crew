"""Real isolated HTTP/SQLite; only the model process is fake."""
import datetime as dt
import json
import os
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_assistant_http import AssistantHttpTests


def main():
    case = AssistantHttpTests('test_explicit_message_uses_shared_chat_delivery_and_keeps_identity')
    case.setUp()
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        sid = case.open_assistant()
        print(json.dumps({'url': case.url, 'session_id': sid, 'home': str(case.home)}), flush=True)
        for line in sys.stdin:
            if line.strip() == 'reply':
                process = case.server.service.chat.processes[sid].process
                process.emit({'type': 'assistant', 'message': {'id': 'assistant-reply', 'content': [
                    {'type': 'text', 'text': '你好，我是跨工作区的助手。已收到这条消息。'}]}})
                case.finish_turn(sid, process)
                print(json.dumps({'reply': True}), flush=True)
            elif line.strip() == 'shutdown':
                break
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
