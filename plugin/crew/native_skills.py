"""List the skills a session's own carrier can invoke, through each carrier's native discovery.

Claude Code answers an ``initialize`` control request with its slash commands;
Codex app-server answers ``skills/list``. Both probes are short-lived processes
that never start a model turn: the Claude probe disables hooks (so Crew never
registers it as a session) and keeps no transcript. Results are cached briefly
per carrier and directory so reopening the picker stays instant.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import codex_client, rules

CACHE_SECONDS = 120
PROBE_TIMEOUT = 30

_cache, _lock = {}, threading.Lock()


def _claude_probe(cwd, plugin_root, process_factory):
    from .chat import claude_executable
    command = [claude_executable(), '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--verbose', '--no-session-persistence', '--settings', json.dumps({'disableAllHooks': True}),
               '--plugin-dir', str(Path(plugin_root).resolve())]
    env = {key: value for key, value in os.environ.items()
           if key not in ('CLAUDECODE', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT', 'CREW_SESSION_ID')}
    process = process_factory(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    timer = threading.Timer(PROBE_TIMEOUT, process.kill)
    timer.start()
    try:
        process.stdin.write(json.dumps({'type': 'control_request', 'request_id': 'crew-skills',
                                        'request': {'subtype': 'initialize'}}) + '\n')
        process.stdin.flush()
        for line in process.stdout:
            record = json.loads(line)
            if record.get('type') != 'control_response' or record['response'].get('request_id') != 'crew-skills':
                continue
            response = record['response']
            if response['subtype'] != 'success':
                raise RuntimeError('Claude Code 没有返回技能清单：' + str(response.get('error') or response['subtype']))
            return [{'name': item['name'], 'description': item.get('description') or '',
                     'hint': item.get('argumentHint') or '', 'token': '/' + item['name']}
                    for item in response['response']['commands']
                    if not item.get('builtin') and item['name'] not in rules.SKILLS]
        raise RuntimeError('Claude Code 提前退出，没有返回技能清单：' + (process.stderr.read() or '').strip()[-500:])
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def _codex_probe(cwd, codex_home, process_factory):
    env = dict(os.environ)
    if codex_home is not None:
        env['CODEX_HOME'] = str(codex_home)
    with codex_client.Client(cwd=cwd, env=env, process_factory=process_factory, timeout=PROBE_TIMEOUT) as client:
        result = client.request('skills/list', {'cwds': [cwd]})
    skills = []
    for entry in result['data']:
        skills += [{'name': item['name'], 'description': (item.get('interface') or {}).get('shortDescription')
                    or item.get('shortDescription') or item.get('description') or '',
                    'hint': '', 'token': '$' + item['name']}
                   for item in entry['skills'] if item['enabled']]
    return skills


def list_skills(runtime, cwd, plugin_root, *, codex_home=None, refresh=False, process_factory=None, now=None):
    """Return {'runtime', 'skills', 'cached'} for one carrier in one directory."""
    if runtime not in ('claude', 'codex'):
        raise ValueError('未知执行载体：%s' % runtime)
    cwd = str(Path(cwd).resolve())
    key = (runtime, os.path.normcase(cwd), None if codex_home is None else os.path.normcase(str(codex_home)))
    moment = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(key)
    if hit is not None and not refresh and moment - hit[0] < CACHE_SECONDS:
        return {'runtime': runtime, 'skills': hit[1], 'cached': True}
    factory = process_factory or subprocess.Popen
    skills = (_claude_probe(cwd, plugin_root, factory) if runtime == 'claude'
              else _codex_probe(cwd, codex_home, factory))
    skills.sort(key=lambda item: item['name'].lower())
    with _lock:
        _cache[key] = (moment, skills)
    return {'runtime': runtime, 'skills': skills, 'cached': False}


def clear_cache():
    with _lock:
        _cache.clear()
