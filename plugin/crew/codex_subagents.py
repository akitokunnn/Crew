"""Observe native Codex children without creating Crew roster identities.

The app-server's collabAgentToolCall/subAgentActivity items describe delegation,
not model turns: completing spawnAgent must never mark the child completed.
Only explicit child state is used. Snapshots are rebuilt from the chat journal.
"""
from .subagents import title


STATUSES = {'pendingInit': 'running', 'running': 'running', 'completed': 'succeeded',
            'errored': 'failed', 'interrupted': 'interrupted', 'shutdown': 'closed',
            'notFound': 'unknown'}


class Events:
    def __init__(self, root_thread_id, saved=()):
        self.root = root_thread_id
        self.agents = {task['agent_id']: {key: value for key, value in task.items()
                                        if key not in ('started_at', 'ended_at', 'updated_at')}
                       for task in saved if task['root_thread_id'] == self.root and task.get('runtime', 'codex') == 'codex'}
        self.turns = {}
        self.finished_items = set()

    def _known(self, identity):
        return identity == self.root or identity in self.agents

    def _update(self, identity, parent=None, **fields):
        previous = self.agents.get(identity)
        if identity == self.root or previous is None and not self._known(parent):
            return []
        task = dict(previous or {'agent_id': identity, 'root_thread_id': self.root,
                                'parent_thread_id': parent, 'task_name': 'Codex 子代理 ' + identity[-8:],
                                'status': 'unknown', 'detail': None})
        task.update(fields)
        if task == previous:
            return []
        self.agents[identity] = task
        return [{'type': 'subagent', **task, 'runtime': 'codex'}]

    def _item(self, thread, item, completed):
        if item['id'] in self.finished_items:
            return []
        if item['type'] == 'subAgentActivity':
            self.finished_items.add(item['id'])
            identity, kind = item['agentThreadId'], item['kind']
            fields = {'task_name': title(item['agentPath'].rstrip('/').rsplit('/', 1)[-1])}
            status = {'started': 'running',
                      'interrupted': 'interrupted', 'completed': 'succeeded'}.get(kind)
            previous = self.agents.get(identity, {})
            # An activity summary must not erase a more precise failure report.
            if status and not (kind == 'completed' and previous.get('status') in ('failed', 'interrupted')):
                fields.update(status=status, detail=None)
            return self._update(identity, thread, **fields)
        if item['type'] != 'collabAgentToolCall' or item['senderThreadId'] != thread:
            return []
        if completed:
            self.finished_items.add(item['id'])
        events = []
        states = item.get('agentsStates', {})
        identities = dict.fromkeys([*item.get('receiverThreadIds', []), *states])
        for identity in identities:
            if identity not in self.agents and item['tool'] != 'spawnAgent':
                continue
            fields = {}
            state = states.get(identity)
            if state is not None:
                status = STATUSES.get(state['status'], 'unknown')
                fields.update(status=status, detail=(state.get('message') or state['status'])
                              if status in ('failed', 'interrupted', 'unknown') else None)
            elif item['status'] == 'completed':
                status = {'spawnAgent': 'running', 'resumeAgent': 'running', 'sendInput': 'running',
                          'followupTask': 'running', 'closeAgent': 'closed'}.get(item['tool'])
                if status:
                    fields.update(status=status, detail=None)
            if item['tool'] in ('spawnAgent', 'followupTask') and item.get('prompt'):
                # Prompts include inherited Crew instructions, not a child name.
                fields['task_description'] = item['prompt']
            events.extend(self._update(identity, thread, **fields))
        return events

    def consume(self, method, params):
        if method == 'thread/started':
            thread = params['thread']
            source = thread.get('source')
            spawn = (source.get('subAgent', {}).get('thread_spawn', {})
                     if isinstance(source, dict) and isinstance(source.get('subAgent'), dict) else {})
            parent = thread.get('parentThreadId') or spawn.get('parent_thread_id')
            if not self._known(parent):
                return []
            fields = {}
            name = title(thread.get('name')) or title(thread.get('agentNickname'))
            if name:
                fields['task_name'] = name
            return self._update(thread['id'], parent, **fields)
        thread = params.get('threadId')
        if not self._known(thread):
            return []
        turn = params.get('turn', {})
        turn_id = params.get('turnId') or turn.get('id')
        if method == 'turn/started':
            self.turns[thread] = turn_id
        elif turn_id and thread in self.turns and turn_id != self.turns[thread]:
            return []
        events = []
        if method in ('item/started', 'item/completed'):
            events.extend(self._item(thread, params['item'], method == 'item/completed'))
        elif method == 'turn/completed':
            for item in turn.get('items', []):
                events.extend(self._item(thread, item, True))
        if thread == self.root:
            return events
        if method == 'turn/started':
            events.extend(self._update(thread, status='running', detail=None))
        elif method == 'turn/completed':
            status = {'completed': 'succeeded', 'failed': 'failed', 'interrupted': 'interrupted'}.get(turn['status'], 'unknown')
            events.extend(self._update(thread, status=status, detail=(turn.get('error') or {}).get('message')))
        elif method == 'thread/status/changed':
            status = {'active': 'running', 'systemError': 'failed'}.get(params['status']['type'])
            if status:
                detail = (self.agents[thread].get('detail') or 'Codex 子代理发生系统错误') if status == 'failed' else None
                events.extend(self._update(thread, status=status, detail=detail))
        elif method == 'thread/closed':
            events.extend(self._update(thread, status='closed'))
        elif method == 'thread/name/updated' and title(params.get('threadName')):
            events.extend(self._update(thread, task_name=title(params['threadName'])))
        return events
