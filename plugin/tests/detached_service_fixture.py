"""Real isolated Crew HTTP service with an explicitly injected fake CC carrier.

Only desktop lifecycle tests launch this entrypoint. Native output is released
by unique test marker files after the frontend process has actually exited.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest import mock

from crew import web
from crew.service_lock import HomeServiceLock
from test_chat import FakeProcess


PLUGIN = Path(__file__).resolve().parents[1]


class ControlledCarrier(FakeProcess):
    def __init__(self, home, command, **options):
        super().__init__(command, **options)
        self.inputs, self.finished = [], 0
        original, carrier = self.stdin, self

        class Input:
            pending = ''

            def __getattr__(self, name):
                return getattr(original, name)

            def write(self, text):
                result = original.write(text)
                self.pending += text
                while '\n' in self.pending:
                    line, self.pending = self.pending.split('\n', 1)
                    if not line:
                        continue
                    value = json.loads(line)
                    if value.get('type') == 'user':
                        carrier.inputs.append(value)
                        with (home / 'fake-carrier-inputs.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps({'index': len(carrier.inputs), 'type': 'user'}) + '\n')
                return result

        self.stdin = Input()

        def release():
            while self.poll() is None:
                marker = home / ('release-turn-' + str(self.finished + 1))
                if len(self.inputs) > self.finished and marker.exists():
                    self.finished += 1
                    self.emit({'type': 'assistant', 'message': {'id': 'fixture-result-' + str(self.finished),
                               'content': [{'type': 'text', 'text': 'Recorded synthetic result ' + str(self.finished)}]}})
                    self.emit({'type': 'result', 'is_error': False})
                time.sleep(.01)

        threading.Thread(target=release, daemon=True, name='fake-carrier-release').start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--isolated-sources', action='store_true')
    args = parser.parse_args()
    if not args.isolated_sources:
        raise RuntimeError('This fixture requires isolated sources')
    home = Path(args.home).resolve()
    lock = HomeServiceLock(home)
    if not lock.acquire():
        return 75
    actual_which = shutil.which
    try:
        with mock.patch('crew.native_process.claude_executable', return_value='fixture-claude'), \
             mock.patch('crew.settings.shutil.which', side_effect=lambda name: 'fixture-claude' if name == 'claude' else actual_which(name)):
            with web.create_server(home, PLUGIN, args.port, cwd=home, isolated_sources=True,
                                   agents_provider=lambda: [], claude_root=home / 'isolated' / 'claude',
                                   codex_root=home / 'isolated' / 'codex', runs_file=home / 'isolated' / 'runs.jsonl',
                                   vscode_state_db=home / 'isolated' / 'vscode.db',
                                   process_factory=lambda command, **options: ControlledCarrier(home, command, **options)) as server:
                def shutdown():
                    for line in sys.stdin:
                        if line.strip() == 'shutdown':
                            break
                    server.shutdown()

                threading.Thread(target=shutdown, daemon=True, name='fixture-service-control').start()
                print(server.url, flush=True)
                server.serve_forever(poll_interval=.03)
        return 0
    finally:
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
