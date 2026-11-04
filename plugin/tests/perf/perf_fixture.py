"""Opt-in heavy-user fixture. Never uses a real carrier or an existing HOME."""
import argparse
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[2]
ROOT = PLUGIN.parent
sys.path[:0] = [str(PLUGIN), str(PLUGIN / 'tests')]
for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(key, None)
import tests  # Production-port audit guard.
from crew import board, chat, goals, runtimes, settings, store, web
from crew.runtime_catalog import Catalog
from test_chat import FakeProcess
from test_codex_runtime import FakeClient
from test_shell import make_vscode_state_db
from tests.fixture_carriers import install_lookup

STAMP = '2026-11-05T00:00:00.000Z'


@dataclass
class Scale:
    workspaces: int = 5
    sessions: int = 40
    goals: int = 300
    todos: int = 200
    decisions: int = 100
    runs: int = 2000
    tiers: tuple = (2000, 20000, 100000)
    previous: int = 3
    journal_events: int = 20000
    large_output_bytes: int = 20480


def arguments(parser):
    for key, value in asdict(Scale()).items():
        if key == 'tiers':
            parser.add_argument('--tiers', type=int, nargs=3, default=value)
        else:
            parser.add_argument('--' + key.replace('_', '-'), type=int, default=value)


def scale_from(args):
    return Scale(**{key: getattr(args, key) for key in asdict(Scale())})


def events(count, large_output_bytes=20480):
    """20-event turns: 5% human, 65% text, 10% assistant, 5% result,
    10% status, 5% turn_result. Every fifth tool result has 20 KiB output.
    Tool calls use the persisted assistant.content protocol, not a made-up type.
    """
    for index in range(count):
        turn, slot = divmod(index, 20)
        event = {'seq': index + 1, 'at': STAMP}
        if slot == 0:
            event.update(type='message', id=f'user-{turn}', source='human', text=f'核对性能样本 {turn}')
        elif slot in (1, 19):
            event.update(type='status', status='working' if slot == 1 else 'idle')
        elif slot == 9:
            event.update(type='assistant', id=f'answer-{turn}', content=[
                {'type': 'text', 'text': '正在检查日志与接口。' * 7},
                {'type': 'tool_use', 'id': f'tool-{turn}', 'name': 'Read',
                 'input': {'file_path': 'sample.txt'}}])
        elif slot == 10:
            event.update(type='tool_result', tool_use_id=f'tool-{turn}', is_error=False,
                         content='x' * large_output_bytes if turn % 5 == 0 else 'checked sample')
        elif slot == 17:
            event.update(type='assistant', id=f'final-{turn}',
                         content=[{'type': 'text', 'text': '检查完成，保留原始证据。' * 6}])
        elif slot == 18:
            event.update(type='turn_result', status='completed')
        else:
            event.update(type='text', id=f'answer-{turn}' if slot < 9 else f'final-{turn}',
                         text='正在检查日志与接口。' if slot < 9 else '检查完成，保留原始证据。')
        yield event


def write_journal(path, count, large_output_bytes=20480):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        for event in events(count, large_output_bytes):
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')


class ReplyProcess(FakeProcess):
    """Existing fake process, with a fixed 10 ms synthetic assistant response."""
    def __init__(self, command, **options):
        super().__init__(command, **options)
        original_write = self.stdin.write

        def write(value):
            size = original_write(value)
            record = json.loads(value)
            if record['type'] == 'user':
                timer = threading.Timer(.01, self.reply, args=(record['uuid'],))
                timer.daemon = True
                timer.start()
            return size
        self.stdin.write = write

    def reply(self, identity):
        self.emit({'type': 'stream_event', 'api_message_id': 'reply-' + identity,
                   'event': {'type': 'content_block_delta', 'delta': {
                       'type': 'text_delta', 'text': 'PERF_ASSISTANT_' + identity}}})
        self.emit({'type': 'result', 'is_error': False})


class ReplyClient(FakeClient):
    counter = 0

    def __init__(self, **options):
        super().__init__(**options)
        type(self).counter += 1
        self.thread_id = 'perf-native-' + str(type(self).counter)

    def start_turn(self, **params):
        result = super().start_turn(**params)
        def reply():
            self.emit('item/agentMessage/delta', turnId=self.turn_id, itemId='perf-answer', delta='PERF_ASSISTANT_CODEX')
            self.finish()
        timer = threading.Timer(.01, reply)
        timer.daemon = True
        timer.start()
        return result


class CatalogClient:
    def __init__(self, **options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def list_models(self, **args):
        return {'data': [{'id': 'perf-fake', 'model': 'perf-fake', 'displayName': 'Perf fake',
                          'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}],
                          'defaultReasoningEffort': 'medium'}], 'nextCursor': None}


class Fixture:
    def __init__(self, scale=None):
        self.scale = Scale() if scale is None else scale
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='crew-perf-')))
        self.home = self.root / 'home'
        self.processes = []

    def addCleanup(self, callback):
        self.stack.callback(callback)

    def __enter__(self):
        # Ensure setup failures release the fixture too, using ExitStack ownership.
        self.stack.__enter__()
        return self

    def build(self, *, serve=True):
        s = self.scale
        if s.workspaces < 1 or s.sessions < 6 or s.sessions < s.workspaces * 2 or s.goals < 1:
            raise ValueError('Need >=1 workspace, >=6 sessions, >=2 sessions/workspace and >=1 goal')
        install_lookup(self, self.root)
        self.stack.enter_context(mock.patch('crew.codex_runtime._ObservedClient', ReplyClient))
        self.stack.enter_context(mock.patch('crew.runtime_catalog.Catalog',
            new=lambda: Catalog(client_factory=CatalogClient,
                                which=lambda name: str(self.root / 'fake-carriers' / (name + '.exe')))))
        # Freeze autonomous scheduling only. HTTP, scan_usage, SQLite, journals,
        # serialization and send remain production implementations.
        self.stack.enter_context(mock.patch.object(web.Service, 'start', new=lambda service: service.refresh_agents()))
        claude, codex = self.root / 'claude', self.root / 'codex'
        claude.mkdir()
        codex.mkdir()
        vscode = make_vscode_state_db(self.root / 'vscode.sqlite')
        self.projects, self.sessions, self.leaders = [], [], []
        with closing(store.connect(self.home)) as conn:
            settings.save(conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
            for index in range(s.workspaces):
                name = f'perf-{index}'
                directory = self.root / name
                directory.mkdir()
                board.init_project(conn, name, directory, now=STAMP)
                self.projects.append(name)
            counts = [s.sessions // s.workspaces + (i < s.sessions % s.workspaces) for i in range(s.workspaces)]
            for index, count in enumerate(counts):
                project = self.projects[index]
                directory = self.root / project
                for local in range(count):
                    sid = f'{project}-s{local}'
                    leader = local < (count + 1) // 2
                    board.join(conn, sid, project, sid, directory, directory, None,
                               role='leader' if leader else 'member',
                               group_name=None if leader else f'{project}-s{local % ((count + 1) // 2)}', now=STAMP)
                    runtimes.set(conn, sid, 'claude' if local % 2 == 0 else 'codex',
                                 native_id=sid if local % 2 == 0 else 'fake-' + sid)
                    self.sessions.append(sid)
                    if leader:
                        self.leaders.append(sid)
            # Three leaders in the first project at the default scale.
            self.history_sessions = self.leaders[:3]
            for sid in self.sessions:
                count = s.tiers[self.history_sessions.index(sid)] if sid in self.history_sessions else s.tiers[0]
                write_journal(self.home / 'chat' / (sid + '.jsonl'), count, s.large_output_bytes)
            goal_ids = []
            states = sorted(goals.STATES)
            for index in range(s.goals):
                sid = self.leaders[index % len(self.leaders)]
                project = board.session(conn, sid)['project']
                goal = goals.create(conn, project, sid, f'性能任务 {index}', '核对原始数据与结果', ['提供证据'], now=STAMP)
                goal_ids.append(goal['id'])
                conn.execute('UPDATE goals SET status=?,manual_pause=? WHERE id=?',
                             (states[index % len(states)], int(states[index % len(states)] == 'paused'), goal['id']))
            self.task_goal, self.task_session = goal_ids[0], self.leaders[0]
            conn.execute("UPDATE goals SET status='paused',manual_pause=1 WHERE id=?", (self.task_goal,))
            self.executions = []
            for index in range(s.previous + 1):
                # Existing model tables, no schema changes; fixture generations are idle.
                key = f'perf-execution-{index}'
                conn.execute('INSERT INTO goal_executions(id,goal_id,round,session_id,generation,runtime,status,'
                             'requirement_version,created_at,updated_at) VALUES(?,?,1,?,?,?,\'idle\',1,?,?)',
                             (key, self.task_goal, self.task_session, index + 1, 'claude', STAMP, STAMP))
                write_journal(self.home / 'chat' / (key + '.jsonl'), s.tiers[1], s.large_output_bytes)
                self.executions.append(key)
            for index in range(s.todos):
                sid = self.sessions[index % len(self.sessions)]
                project = board.session(conn, sid)['project']
                conn.execute('INSERT INTO todos(project,session_id,title,steps,verification,created_at,updated_at) '
                             'VALUES(?,?,?,?,?,?,?)', (project, sid, f'人工核对 {index}', '核对样本', '提供截图', STAMP, STAMP))
            for index in range(s.decisions):
                sid = self.leaders[index % len(self.leaders)]
                conn.execute('INSERT INTO decisions(project,session_id,question,options,recommendation,created_at) '
                             'VALUES(?,?,?,?,?,?)', (board.session(conn, sid)['project'], sid,
                             f'确认采样方案 {index}', '["方案 A", "方案 B"]', '方案 A', STAMP))
            conn.commit()
        runs = self.home / 'codex_runs.jsonl'
        with runs.open('w', encoding='utf-8') as stream:
            for index in range(s.runs):
                sid = self.sessions[index % len(self.sessions)]
                stream.write(json.dumps({'run_id': f'perf-run-{index}', 'crew_session_id': sid,
                    'task_file': 'performance-sample.md', 'workdir': str(self.root / sid.rsplit('-s', 1)[0]),
                    'model': 'perf-fake', 'effort': 'medium',
                    'started_at': STAMP, 'ended_at': STAMP, 'exit_code': 0}) + '\n')
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.root / self.projects[0],
            agents_provider=lambda: [], claude_root=claude, codex_root=codex, runs_file=runs,
            process_factory=self.process_factory, vscode_state_db=vscode, isolated_sources=True,
            lock_status=lambda: False)
        self.stack.callback(self.server.server_close)
        if serve:
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
            self.thread.start()
            self.stack.callback(self.stop_server)
        self.url = self.server.url
        self.metadata = {'url': self.url, 'home': str(self.home), 'scale': asdict(s),
            'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'revision': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], check=True,
                                       capture_output=True, text=True).stdout.strip(),
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                             ('plugin/crew/chat.py', 'plugin/crew/web.py', 'plugin/crew/web/chat.js', 'plugin/crew/web/app.js')},
            'projects': self.projects, 'history_sessions': self.history_sessions,
            'task_goal': self.task_goal, 'task_session': self.task_session, 'executions': self.executions,
            'send_sessions': {'claude': self.leaders[0], 'codex': self.leaders[1]},
            'carrier': 'FakeProcess / FakeClient only; synthetic reply delay 10 ms',
            'background': 'autonomous workers frozen; real HTTP/state/storage/send',
            'machine': {'node': platform.node(), 'platform': platform.platform(),
                        'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
                        'python': sys.version},
            'journal_bytes': {p.stem: p.stat().st_size for p in (self.home / 'chat').glob('*.jsonl')}}
        return self

    def process_factory(self, command, **options):
        process = ReplyProcess(command, **options)
        self.processes.append(process)
        return process

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(5)
        assert not self.thread.is_alive()

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    args = parser.parse_args()
    with Fixture(scale_from(args)) as fixture:
        fixture.build()
        print(json.dumps(fixture.metadata, ensure_ascii=False), flush=True)
        for line in sys.stdin:
            command = line.strip()
            if command == 'shutdown':
                break
            if command == 'replace-history':
                sid = fixture.history_sessions[1]
                fixture.server.service.chat.path(sid).write_text(json.dumps({
                    'seq': 1, 'type': 'message', 'id': 'replacement', 'source': 'human',
                    'text': 'PERF_REPLACEMENT_JOURNAL', 'at': STAMP}) + '\n', encoding='utf-8')
                print(json.dumps({'replaced': sid, 'latest_seq': 1}), flush=True)
                continue
            if command != 'metadata':
                raise ValueError('Unknown fixture command: ' + command)
            print(json.dumps(fixture.metadata, ensure_ascii=False), flush=True)
    print(json.dumps({'closed': True}), flush=True)


if __name__ == '__main__':
    main()
