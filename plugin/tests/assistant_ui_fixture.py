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
        call_started = False
        for line in sys.stdin:
            command = line.strip()
            if command.startswith('tokens '):
                process = case.server.service.chat.processes[sid].process
                if not call_started:
                    process.emit({'type': 'stream_event', 'api_message_id': 'assistant-reply', 'event': {
                        'type': 'message_start', 'message': {'id': 'assistant-reply'}}})
                    process.emit({'type': 'stream_event', 'api_message_id': 'assistant-reply', 'event': {
                        'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}})
                    call_started = True
                count = int(command.split()[1])
                process.emit({'type': 'stream_event', 'api_message_id': 'assistant-reply', 'event': {
                    'type': 'message_delta', 'usage': {'output_tokens': count}}})
                journal = case.server.service.chat.journal(sid)
                with journal.condition:
                    case.assertTrue(journal.condition.wait_for(
                        lambda: journal.progress and journal.progress['output_tokens'] == count, timeout=3))
                print(json.dumps({'tokens': count}), flush=True)
            elif command == 'reply':
                process = case.server.service.chat.processes[sid].process
                process.emit({'type': 'assistant', 'message': {'id': 'assistant-reply', 'content': [
                    {'type': 'text', 'text': '你好，我是跨工作区的助手。已收到这条消息。'}]}})
                case.finish_turn(sid, process)
                call_started = False
                print(json.dumps({'reply': True}), flush=True)
            elif command == 'fail':
                process = case.server.service.chat.processes[sid].process
                process.emit({'type': 'result', 'is_error': True, 'errors': ['测试模型响应失败：连接已断开']})
                journal = case.server.service.chat.journal(sid)
                with journal.condition:
                    case.assertTrue(journal.condition.wait_for(lambda: journal.status == 'idle', timeout=3))
                call_started = False
                print(json.dumps({'failed': True}), flush=True)
            elif command == 'shutdown':
                break
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
