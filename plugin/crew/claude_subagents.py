"""Claude Agent/Task calls and SDK task_* events, separate from parent turns."""
from .subagents import title


STATES = {'pending': 'running', 'running': 'running', 'completed': 'succeeded',
          'failed': 'failed', 'stopped': 'interrupted', 'killed': 'interrupted', 'paused': 'interrupted'}


def text(content):
    return content if isinstance(content, str) else '\n'.join(block.get('text', '') for block in content or []
                                                            if isinstance(block, dict) and block.get('type') == 'text')


class Events:
    def __init__(self, root, saved=()):
        self.root, self.agents, self.calls, self.tasks = root, {}, {}, {}
        self.results, self.replacements = set(), {}
        self.restored = []
        for task in saved:
            if task.get('runtime') != 'claude' or task['root_thread_id'] != root:
                continue
            task = {key: value for key, value in task.items() if key not in ('started_at', 'ended_at', 'updated_at')}
            identity = task['agent_id']
            self.agents[identity] = task
            if task.get('tool_use_id'):
                self.calls[task['tool_use_id']] = identity
            if task.get('native_task_id'):
                self.tasks[task['native_task_id']] = identity
            if task['status'] == 'running':
                self.restored.extend(self._update(identity, status='unknown', detail='会话重新连接，子代理状态未确认'))

    def _bind(self, call=None, task=None):
        left, right = self.calls.get(call), self.tasks.get(task)
        identity = left or right or 'claude:' + self.root + ':' + (call or task)
        if left and right and left != right:
            other = self.agents.pop(right)
            self.agents[left] = {**self.agents[left], **other, 'agent_id': left}
            self.replacements[left] = right
            self.tasks = {key: left if value == right else value for key, value in self.tasks.items()}
            self.calls = {key: left if value == right else value for key, value in self.calls.items()}
        if call:
            self.calls[call] = identity
        if task:
            self.tasks[task] = identity
        return identity

    def _update(self, identity, **fields):
        previous = self.agents.get(identity)
        task = {**(previous or {'agent_id': identity, 'root_thread_id': self.root,
                  'parent_thread_id': self.root, 'runtime': 'claude',
                  'task_name': 'Claude 子代理 ' + identity.rsplit(':', 1)[-1][-8:],
                  'status': 'unknown', 'detail': None}), **fields}
        replaced = self.replacements.pop(identity, None)
        if task == previous and replaced is None:
            return []
        self.agents[identity] = task
        return [{'type': 'subagent', **task, **({'replaces_agent_id': replaced} if replaced else {})}]

    def consume(self, record):
        if record.get('session_id') not in (None, self.root):
            return []
        events, self.restored = self.restored, []
        kind = record['type']
        if kind == 'assistant':
            parent_call = record.get('parent_tool_use_id')
            if parent_call and parent_call not in self.calls:
                return events
            for block in record['message']['content']:
                if block['type'] != 'tool_use' or block['name'] not in ('Agent', 'Task') or block['id'] in self.calls:
                    continue
                args = block['input']
                identity = self._bind(call=block['id'])
                fields = {'task_description': args.get('prompt')}
                if title(args.get('description')):
                    fields['task_name'] = title(args['description'])
                events.extend(self._update(identity, tool_use_id=block['id'], **fields,
                    parent_thread_id=self.calls.get(parent_call, self.root),
                    background=bool(args.get('run_in_background')), status='running'))
        elif kind == 'user' and isinstance(record['message']['content'], list):
            result = record.get('tool_use_result')
            result = result if isinstance(result, dict) else {}
            for block in record['message']['content']:
                call = block.get('tool_use_id')
                if block['type'] != 'tool_result' or call not in self.calls or call in self.results:
                    continue
                self.results.add(call)
                identity = self._bind(call, result.get('agentId'))
                task = self.agents[identity]
                fields = {}
                if result.get('agentId'):
                    fields['native_task_id'] = result['agentId']
                if block.get('is_error'):
                    fields.update(status='failed', detail=text(block.get('content')))
                elif result.get('status') == 'async_launched' or result.get('isAsync'):
                    fields['background'] = True
                elif task.get('status') not in ('failed', 'interrupted', 'closed') and (result.get('status') == 'completed' or not task.get('background')):
                    fields.update(status='succeeded', detail=None)
                events.extend(self._update(identity, **fields))
        elif kind == 'system' and record.get('subtype') in ('task_started', 'task_progress', 'task_updated', 'task_notification'):
            if record.get('ambient') or record.get('skip_transcript'):
                return events
            subtype, native = record['subtype'], record['task_id']
            call = record.get('tool_use_id')
            known = native in self.tasks or call in self.calls
            if not known and not (subtype == 'task_started' and
                    (record.get('task_type') in ('local_agent', 'remote_agent') or record.get('subagent_type'))):
                return events
            if record.get('task_type') not in (None, 'local_agent', 'remote_agent'):
                return events
            identity = self._bind(call, native)
            task = self.agents.get(identity, {})
            run = record.get('run_id')
            if run and task.get('run_id') and run < task['run_id']:
                return events
            if not run and task.get('run_id') and subtype != 'task_started':
                return events
            fresh_run = bool(run and run != task.get('run_id'))
            if subtype == 'task_progress' and not fresh_run and task.get('status') in ('succeeded', 'failed', 'interrupted', 'closed'):
                return events
            fields = {'native_task_id': native}
            if call:
                fields['tool_use_id'] = call
            if run:
                fields['run_id'] = run
            patch = record.get('patch', {})
            name = title(patch.get('description') or record.get('description'))
            if name:
                fields['task_name'] = name
            if subtype == 'task_started':
                if task.get('run_id') == run and task.get('status') in ('succeeded', 'failed', 'interrupted', 'closed') and call in (None, task.get('tool_use_id')):
                    return events
                fields.update(status='running', detail=None, background=record.get('is_backgrounded', True))
            elif subtype == 'task_notification':
                status = STATES.get(record['status'], 'unknown')
                if record.get('reason') == 'worker_restart':
                    status = 'unknown'
                fields.update(status=status, detail=record.get('summary') if status != 'succeeded' else None)
            elif subtype == 'task_updated' and patch.get('status'):
                status = STATES.get(patch['status'], 'unknown')
                fields.update(status=status, detail=patch.get('error') if status in ('failed', 'unknown') else None)
            elif subtype == 'task_progress' and (fresh_run or task.get('status') == 'unknown'):
                fields.update(status='running', detail=None)
            if 'is_backgrounded' in patch:
                fields['background'] = patch['is_backgrounded']
            events.extend(self._update(identity, **fields))
        elif kind == 'result' and not record.get('parent_tool_use_id'):
            for identity, agent in list(self.agents.items()):
                if agent['status'] == 'running' and not agent.get('background'):
                    events.extend(self._update(identity, status='unknown', detail='主轮次已结束，子代理终态未确认'))
        return events
