"""Opt-in real-loopback performance baseline. No CI/default discovery hook."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import cProfile
import json
import math
from pathlib import Path
import pstats
import shutil
import subprocess
import sys
import threading
import time
from unittest import mock
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from perf_fixture import Fixture, ROOT, arguments, scale_from, write_journal
from crew import chat, web


def summary(samples, key):
    values = sorted(sample[key] for sample in samples)
    return {'p50': values[math.ceil(len(values) * .50) - 1],
            'p95': values[math.ceil(len(values) * .95) - 1], 'max': values[-1]}


def measure_request(fixture, path, data=None, sse=False):
    payload = None if data is None else json.dumps(data).encode('utf-8')
    request = Request(fixture.url + path, payload, {'X-Crew': '1', 'Content-Type': 'application/json'})
    started = time.perf_counter()
    try:
        response = urlopen(request, timeout=180)
    except HTTPError as error:
        with error:
            detail = error.read().decode('utf-8')
        raise RuntimeError(f'{type(error).__name__}: {error.code} {path}: {detail}') from error
    with response:
        headers_ms = (time.perf_counter() - started) * 1000
        if sse:
            lines = []
            while True:
                line = response.readline()
                if not line:
                    raise EOFError('SSE closed before first data event')
                lines.append(line)
                if line.startswith(b'data:'):
                    break
            body = b''.join(lines)
        else:
            body = response.read()
        ended = time.perf_counter()
        assert response.status == 200
    return {'ms': (ended - started) * 1000, 'headers_ms': headers_ms,
            'bytes': len(body), 'started': started, 'ended': ended}


def summarize(samples):
    return {'ms': summary(samples, 'ms'), 'bytes': summary(samples, 'bytes'), 'samples': samples}


def journal_baseline(fixture, iterations, emits):
    source = fixture.root / 'journal-source.jsonl'
    write_journal(source, fixture.scale.journal_events, fixture.scale.large_output_bytes)
    target = fixture.root / 'journal-write.jsonl'
    original = Path.read_text
    samples = []
    for _ in range(iterations + 1):
        shutil.copyfile(source, target)
        journal = chat.Journal(target)
        reads = []
        def read_text(path, *args, **kwargs):
            if path == target:
                reads.append(path)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', read_text):
            started = time.perf_counter()
            for index in range(emits):
                journal.emit('text', id='perf-stream', text=f'增量 {index}')
            journal.refresh()
            ended = time.perf_counter()
        assert len(journal.events) == fixture.scale.journal_events + emits
        assert [e['seq'] for e in journal.events] == list(range(1, len(journal.events) + 1))
        assert chat.Journal(target).events == journal.events
        samples.append({'ms': (ended - started) * 1000, 'bytes': target.stat().st_size - source.stat().st_size,
                        'rereads': len(reads), 'events': len(journal.events)})
    return {**summarize(samples[1:]), 'rereads': summary(samples[1:], 'rereads'),
            'initial_events': fixture.scale.journal_events, 'emits': emits,
            'external_interleaving': journal_external_interleaving(fixture.root / 'journal-race.jsonl')}


def journal_external_interleaving(path):
    """Diagnostic only: inject an independent file append at the open(a) seam.
    Record existing behavior; do not treat the known mismatch as a passing fix.
    """
    journal = chat.Journal(path)
    journal.emit('text', id='seed', text='seed')
    original_open = Path.open
    external = {'seq': 2, 'type': 'message', 'id': 'external', 'source': 'human',
                'text': 'external during append'}
    def inject(current, mode='r', *args, **kwargs):
        if current == path and mode == 'a':
            with original_open(path, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(external) + '\n')
        return original_open(current, mode, *args, **kwargs)
    with mock.patch.object(Path, 'open', inject):
        journal.emit('text', id='own', text='own')
    journal.refresh()
    persisted = chat.Journal(path).events
    return {'scenario': 'external append after refresh before own open(a)',
            'memory_after_refresh': journal.events, 'persisted_events': persisted,
            'equal': journal.events == persisted, 'source_modified': False}


def concurrent_samples(fixture, path, data, iterations):
    """Release state only once the other request has entered its real handler.
    Record actual server/client overlap; do not inject sleeps or hold product locks.
    """
    original = web.Handler._handle
    results = []
    state_path = '/api/state?project=' + fixture.projects[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(iterations + 1):
            entered = threading.Event()
            timings = {}
            def handle(handler, method):
                if handler.path == path:
                    timings['server_started'] = time.perf_counter()
                    entered.set()
                    try:
                        return original(handler, method)
                    finally:
                        timings['server_ended'] = time.perf_counter()
                if handler.path == state_path:
                    timings['state_server_started'] = time.perf_counter()
                    try:
                        return original(handler, method)
                    finally:
                        timings['state_server_ended'] = time.perf_counter()
                return original(handler, method)
            with mock.patch.object(web.Handler, '_handle', handle):
                work = pool.submit(measure_request, fixture, path, data)
                assert entered.wait(180)
                state = pool.submit(measure_request, fixture, state_path)
                first, second = work.result(), state.result()
            assert 'server_ended' in timings
            overlap = max(0, min(first['ended'], second['ended']) - max(first['started'], second['started'])) * 1000
            handler_overlap = max(0, min(timings['server_ended'], timings['state_server_ended']) -
                                  max(timings['server_started'], timings['state_server_started'])) * 1000
            results.append({'work': first, 'state': second, 'overlap_ms': overlap,
                            'handler_overlap_ms': handler_overlap,
                            'state_admission_ms': (timings['state_server_started'] - second['started']) * 1000,
                            **timings})
    samples = results[1:]
    return {'work': summarize([s['work'] for s in samples]),
            'state': summarize([s['state'] for s in samples]),
            'overlap_ms': summary(samples, 'overlap_ms'),
            'handler_overlap_ms': summary(samples, 'handler_overlap_ms'),
            'state_admission_ms': summary(samples, 'state_admission_ms'), 'samples': samples}


def profiles(args):
    """Run the real HTTP handler in the only Python thread. The HTTP client is
    a separate Python process, avoiding CPython 3.13 cross-thread profile stacks.
    Fresh fixture, one warmup per route; no model work or background scheduling.
    """
    result = json.loads(args.output.read_text(encoding='utf-8'))
    slowest = sorted(result['endpoints'], key=lambda name: result['endpoints'][name]['ms']['p95'], reverse=True)[:3]
    result['profiles'] = []
    with Fixture(scale_from(args)) as fixture:
        fixture.build(serve=False)
        for index, name in enumerate(slowest):
            path = result['endpoints'][name]['path']
            if name == 'history/task':
                path = '/api/chat/history?' + urlencode({'project': fixture.projects[0],
                    'session_id': fixture.task_session, 'goal_id': fixture.task_goal})
            data = ({'session_id': fixture.metadata['send_sessions'][name.split('/')[1]], 'text': 'profile sample'}
                    if name.startswith('send/') else None)
            source = ('import sys,json;sys.path.insert(0,sys.argv[1]);'
                      'from perf_backend import measure_request;'
                      'from types import SimpleNamespace;'
                      'print(json.dumps(measure_request(SimpleNamespace(url=sys.argv[2]),'
                      'sys.argv[3],json.loads(sys.argv[4]),sys.argv[5]=="1")))')
            for warmup in (True, False):
                child = subprocess.Popen([sys.executable, '-c', source, str(Path(__file__).parent),
                    fixture.url, path, json.dumps(data), str(int(name.startswith('sse/')))],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                connection, address = fixture.server.get_request()
                profile = cProfile.Profile()
                try:
                    if not warmup:
                        profile.enable()
                    fixture.server.finish_request(connection, address)
                finally:
                    if not warmup:
                        profile.disable()
                    fixture.server.shutdown_request(connection)
                stdout, stderr = child.communicate(timeout=180)
                assert child.returncode == 0, stderr
                sample = json.loads(stdout)
            destination = args.output.with_name(args.output.stem + f'-profile-{index + 1}.prof')
            profile.dump_stats(str(destination))
            stats = pstats.Stats(profile)
            top = [{'function': f'{filename}:{line} {function}', 'calls': calls,
                    'own_ms': own * 1000, 'cumulative_ms': cumulative * 1000}
                   for (filename, line, function), (_, calls, own, cumulative, _) in sorted(
                       stats.stats.items(), key=lambda entry: entry[1][3], reverse=True)[:15]]
            result['profiles'].append({'endpoint': name, 'path': str(destination), 'sample': sample, 'top15': top,
                'fixture_home': str(fixture.home),
                'mode': 'real HTTP handler on main thread; separate Python HTTP client; fresh fixture; one warmup'})
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--emits', type=int, default=1000)
    parser.add_argument('--output', type=Path, default=ROOT / 'Temp/tasks/perf-evidence/backend.json')
    parser.add_argument('--profiles-only', action='store_true', help='Recollect profiles for an existing result at --output')
    args = parser.parse_args()
    if args.iterations < 1 or args.emits < 1:
        parser.error('iterations and emits must be positive')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.profiles_only:
        profiles(args)
        return
    with Fixture(scale_from(args)) as fixture:
        fixture.build()
        result = {'fixture': fixture.metadata, 'iterations': args.iterations, 'warmups': 1,
                  'endpoints': {}, 'profiles': [], 'concurrency': {},
                  'source_revision': fixture.metadata['revision']}
        cases = []
        for project in fixture.projects:
            cases.append(('state/' + project, '/api/state?' + urlencode({'project': project}), None, False))
        cases += [('home/state', '/api/state', None, False),
                  ('home/projects', '/api/projects', None, False),
                  ('home/assistant', '/api/assistant/state', None, False),
                  ('home/activity', '/api/workspaces/activity?' + urlencode([('project', p) for p in fixture.projects]), None, False)]
        for count, sid in zip(fixture.scale.tiers, fixture.history_sessions):
            project = sid.rsplit('-s', 1)[0]
            query = urlencode({'project': project, 'session_id': sid})
            cases.append((f'history/{count}', '/api/chat/history?' + query, None, False))
            cases.append((f'sse/{count}', '/api/chat/events?' + query + '&after=0', None, True))
        task_query = urlencode({'project': fixture.projects[0], 'session_id': fixture.task_session, 'goal_id': fixture.task_goal})
        cases += [('history/task', '/api/chat/history?' + task_query, None, False),
                  ('sse/task', '/api/chat/events?' + task_query + '&after=0', None, True)]
        for runtime, sid in fixture.metadata['send_sessions'].items():
            cases.append(('send/' + runtime, '/api/chat/send?' + urlencode({'project': sid.rsplit('-s', 1)[0]}),
                          {'session_id': sid, 'text': '性能采样：仅假载体'}, False))
        for name, path, data, sse in cases:
            samples = [measure_request(fixture, path, data, sse) for _ in range(args.iterations + 1)]
            result['endpoints'][name] = {'path': path, **summarize(samples[1:])}
            print(f'{name}: p95={result["endpoints"][name]["ms"]["p95"]:.2f}ms', flush=True)
        result['journal'] = journal_baseline(fixture, args.iterations, args.emits)
        print('journal: ' + json.dumps(result['journal']['ms']), flush=True)
        cases_by_name = {case[0]: case for case in cases}
        for name in ('history/100000' if 100000 in fixture.scale.tiers else f'history/{fixture.scale.tiers[-1]}', 'send/claude', 'send/codex'):
            _, path, data, _ = cases_by_name[name]
            result['concurrency'][name] = concurrent_samples(fixture, path, data, args.iterations)
            print('concurrency ' + name, flush=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(str(args.output), flush=True)
    profiles(args)


if __name__ == '__main__':
    main()
