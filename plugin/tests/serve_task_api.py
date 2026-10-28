"""Opt-in frontend development service: real Crew APIs, synthetic data/carrier."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import threading
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crew import board, settings, store, tasks, web
from crew.service_lock import HomeServiceLock
from test_chat import FakeProcess


class DemoCarrier(FakeProcess):
    """Speaks only synthetic Claude stream JSON; never invokes a CLI."""
    def __init__(self, command, **options):
        if 'app-server' in command:
            raise OSError('此开发夹具仅提供 Claude 协议假载体；Codex 使用其专用协议测试夹具')
        super().__init__(command, **options)
        original, carrier = self.stdin, self
        self.timers = []

        class Input:
            pending = ''

            def __getattr__(self, key):
                return getattr(original, key)

            def write(self, text):
                written = original.write(text)
                self.pending += text
                while '\n' in self.pending:
                    line, self.pending = self.pending.split('\n', 1)
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get('type') == 'user':
                        def reply():
                            if carrier.poll() is None:
                                carrier.emit({'type': 'assistant', 'message': {'id': 'synthetic-' + uuid.uuid4().hex,
                                    'content': [{'type': 'text', 'text': '合成载体已收到这条输入。此服务使用真实 Crew API 和临时数据，没有调用真实 AI，也没有修改项目文件。'}]}})
                                carrier.emit({'type': 'result', 'is_error': False})
                        timer = threading.Timer(.25, reply)
                        timer.daemon = True
                        carrier.timers.append(timer)
                        timer.start()
                    elif event.get('type') == 'control_request' and event.get('request', {}).get('subtype') == 'interrupt':
                        for timer in carrier.timers:
                            timer.cancel()
                        carrier.emit({'type': 'result', 'is_error': True, 'errors': ['合成载体：当前轮已中断']})
                return written

        self.stdin = Input()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True, help='A new, empty temporary directory')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if home == store.default_home().resolve() or home.exists() and any(home.iterdir()):
        parser.error('必须使用全新空目录；夹具不读取、覆盖或复用已有 Crew HOME')
    if args.port in (47811, 7122) or not 0 <= args.port <= 65535:
        parser.error('使用独立端口，不能使用保留端口 47811 或 7122')
    for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID'):
        os.environ.pop(key, None)
    home.mkdir(parents=True, exist_ok=True)
    # Keep editable project files outside Crew HOME, just like a real ordinary
    # workspace. Deliberately retain this unique directory after shutdown.
    project = home.parent / ('crew-api-project-' + uuid.uuid4().hex)
    project.mkdir()
    with closing(store.connect(home)) as conn:
        board.init_project(conn, 'api-demo', project)
        settings.save(conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        task = tasks.create(conn, 'api-demo', '这是合成讨论任务：先澄清效果与完成标准，不授权真实施工。', title='合成讨论示例')
    lock = HomeServiceLock(home)
    if not lock.acquire():
        raise RuntimeError('此临时 HOME 已由另一服务占用')
    try:
        with mock.patch('crew.native_process.claude_executable', return_value='synthetic-claude'), \
             mock.patch('crew.settings.shutil.which', side_effect=lambda name: 'synthetic-claude' if name == 'claude' else None):
            with web.create_server(home, Path(__file__).resolve().parents[1], args.port, cwd=project,
                    isolated_sources=True, agents_provider=lambda: [], claude_root=home / 'native/claude/projects',
                    codex_root=home / 'native/codex/sessions', runs_file=home / 'native/runs.jsonl',
                    vscode_state_db=home / 'native/vscode.db', process_factory=DemoCarrier) as server:
                server.service.runtime_catalog.which = lambda name: 'synthetic-claude' if name == 'claude' else None
                print(json.dumps({'synthetic': True, 'url': server.url, 'home': str(home), 'task_id': task['id'],
                    'project': 'api-demo', 'project_root': str(project), 'source_version': server.service.version,
                    'real_ai_started': False}, ensure_ascii=False), flush=True)
                try:
                    server.serve_forever(poll_interval=.1)
                except KeyboardInterrupt:
                    pass
    finally:
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
