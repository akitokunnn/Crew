"""Shared, replayable task rows for native Claude and Codex children."""
from . import store


def apply_task(tasks, event):
    identity = event['agent_id']
    replaced = tasks.pop(event.get('replaces_agent_id'), {})
    previous = tasks.get(identity, replaced)
    task = {**previous, **{key: value for key, value in event.items()
                          if key not in ('seq', 'type', 'at', 'replaces_agent_id')}}
    restarting = (task['status'] == 'running' and previous.get('status') in ('succeeded', 'failed', 'interrupted', 'closed')
                  or task.get('run_id') and previous.get('run_id') and task['run_id'] != previous['run_id'])
    task['started_at'] = event['at'] if restarting else previous.get('started_at', event['at'])
    task['updated_at'] = event['at']
    if task['status'] == 'running':
        task['ended_at'] = None
    elif task['status'] == 'unknown':
        task['ended_at'] = previous.get('ended_at')
    else:
        task['ended_at'] = previous.get('ended_at') if previous.get('status') == task['status'] else event['at']
    tasks[identity] = task


def snapshot(tasks, active_root, active_runtime='codex'):
    result = []
    for task in tasks.values():
        item = {**task, 'id': 'subagent:' + task['root_thread_id'] + ':' + task['agent_id'],
                'source': 'native_subagent', 'runtime': task.get('runtime', 'codex')}
        if item['status'] == 'running' and (item['root_thread_id'] != active_root or item['runtime'] != active_runtime):
            item.update(status='unknown', detail='原生连接已断开，子代理状态未确认')
        item['duration_seconds'] = ((store.parse_iso(item['ended_at']) - store.parse_iso(item['started_at'])).total_seconds()
                                    if item['ended_at'] else None)
        result.append(item)
    return result


def title(text):
    name = ' '.join((text or '').split())
    return name if len(name) <= 48 else name[:47] + '…'
