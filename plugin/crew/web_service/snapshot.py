import datetime as dt
from contextlib import closing

from .. import board, channel, chat, claude_budget, claude_limits, git_admin, goal_api, goals, models, presentation, runtime_switch, runtimes, settings, store, themes, todo, trust_mode, usage
from .. import appearance, member_defaults
from .. import session_presence
from .. import event_sounds
from .. import domains


class SnapshotMixin:
    def state(self, project_name=None):
        usage_scanned_at = self.scan_usage(live_dispatch=True)
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
        agents = session_presence.external_agents(self.chat, agents)
        with closing(store.connect(self.home)) as conn:
            result = {'project': None, 'projects': [{key: p[key] for key in ('name', 'display_name', 'theme')} for p in board.projects(conn)],
                      'themes': themes.THEMES,
                      'models': models.MODELS, 'efforts': models.EFFORTS,
                      'runtimes': self.runtime_catalog.snapshot(),
                      'ai': settings.load(conn),
                      'now': self.now().isoformat(), 'agents_error': error, 'notification': self.notification_status(),
                      'leaders': [], 'members': [], 'decisions': [], 'attention': [], 'git_assistant': None, 'git_branches': None, 'domains': None,
                      'todos': [], 'todo_count': 0, 'goals': [], 'native_approvals': [],
                      'candidates': {'running': [], 'others': []},
                      'channel': {'unread': 0, 'latest_id': 0, 'digesting': False, 'digest_error': None, 'digest_id': None},
                      'limits': self.limits(conn), 'usage_scanned_at': usage_scanned_at.isoformat()}
            result['vscode_error'] = self.vscode_error
            histories = {}
            if project_name is not None:
                name = self.selected_project(conn, project_name)
                conversations, histories = self._project_conversations(conn, name, agents, error)
                result.update(conversations)
            def sound_history(identity):
                if identity in histories:
                    return histories[identity]
                return self.chat.history(identity) if self.chat.path(identity).is_file() else None
            result['sounds'] = event_sounds.snapshot(conn, self.home, result['project']['name'] if result['project'] else None, sound_history, self.chat.journal)
            return result

    def member_execution_records(self, conn, session_id, active):
        """A member's task executions for its own page: current ones first, then the most recent."""
        order = {item['id']: index for index, item in enumerate(active)}
        rows = conn.execute(
            'SELECT e.id,e.goal_id,g.title,e.status,e.runtime,e.model,e.effort,e.speed,b.native_id '
            'FROM goal_executions e JOIN goals g ON g.id=e.goal_id '
            'LEFT JOIN goal_execution_bindings b ON b.execution_id=e.id AND b.runtime=e.runtime '
            'WHERE e.session_id=? AND e.round=g.current_round AND e.generation=(SELECT MAX(x.generation) '
            'FROM goal_executions x WHERE x.goal_id=e.goal_id AND x.round=e.round AND x.session_id=e.session_id) '
            'ORDER BY e.updated_at DESC,e.id', (session_id,)).fetchall()
        return [
            {'goal_id': row['goal_id'], 'title': row['title'], 'execution_id': row['id'],
             'status': row['status'], 'active': row['id'] in order,
             'runtime': row['runtime'],
             'settings': self._execution_dispatch_settings(conn, row['id'], row['runtime'], row['native_id'], dict(row))}
            for row in sorted(rows, key=lambda item: order.get(item['id'], len(order)))[:8]
        ]

    def _execution_dispatch_settings(self, conn, execution_id, runtime, native_id, requested):
        observed = usage.agent_settings(conn, runtime, native_id)
        applied = self.chat.applied(execution_id) or {}
        return usage.displayed_settings({**observed, **{key: value for key, value in applied.items() if value}}, requested)

    def _conversation_dispatch(self, conn, task, goal_id):
        runtime = task.get('runtime', 'codex')
        if task.get('source') == 'skill_dispatch':
            observed = usage.agent_settings(conn, runtime, task.get('native_id'))
        elif runtime == 'codex':
            observed = {**usage.agent_settings(conn, runtime, task['agent_id']), **task.get('applied', {})}
        else:
            observed = usage.agent_settings(conn, runtime, task['root_thread_id'], task['native_task_id']) if task.get('native_task_id') else {}
        return {'id': task.get('id') or task['run_id'], 'goal_id': goal_id, 'runtime': runtime,
                'task_name': task['task_name'], 'status': task['status'], 'started_at': task['started_at'],
                **usage.displayed_settings(observed, task.get('requested', {}))}

    def _native_context_history(self, conn, runtime, native_id):
        with self.scan_lock:
            if runtime == 'codex':
                return usage.codex_context(conn, self.codex_root, native_id, self.codex_context_cache)
            usage.session_titles(self.claude_root, [native_id], self.title_cache)
            return self.title_cache.get(native_id, {}).get('context')

    def _project_conversations(self, conn, name, agents, error):
        general_present = board.live_sessions(agents)
        result = {'leaders': [], 'members': [], 'decisions': [], 'git_assistant': None}
        result['goals'] = [goal_api.card(conn, goal) for goal in goals.list_goals(conn, name)]
        card = channel.digest(conn, name)
        result['channel'] = {**channel.state(conn, name), **self.channel_digest.state(name),
                             'digest_id': card['id'] if card is not None else None}
        status, present, agents = session_presence.project_status(self, conn, name, agents, error)
        git_config = board.git_config(conn, name)
        result['project'] = {key: status['project'][key] for key in ('name', 'display_name', 'root', 'theme')}
        result['todos'] = todo.list(conn, name)
        links = {(row['kind'], row['item_id']): row['goal_id'] for row in conn.execute(
            'SELECT l.kind,l.item_id,l.goal_id FROM goal_links l JOIN goals g ON g.id=l.goal_id WHERE g.project=?', (name,))}
        result['todo_count'] = len(result['todos'])
        open_groups = {group['name'] for group in status['groups'] if group['closed_at'] is None}
        removed = {r[0] for r in conn.execute('SELECT session_id FROM session_controls WHERE removed=1')}
        sessions = [s for s in [*status['ungrouped'], *(s for group in status['groups'] for s in group['members'])]
                    if s['session_id'] not in removed and s['status'] != 'closed']
        visible_leaders = {s['session_id'] for s in sessions if s['role'] == 'leader'}
        result['goals'] = [goal for goal in result['goals'] if goal['leader_session_id'] in visible_leaders]
        member_choices = member_defaults.for_project(conn, name)
        looks = appearance.all_looks(conn)
        with self.scan_lock:
            titles = usage.session_titles(self.claude_root, [s['session_id'] for s in sessions], self.title_cache)
        result['native_approvals'] = self.native_approvals(name)
        histories, active_scopes, active_goals, active_goal_ids = {}, {}, {}, {}
        for session in sessions:
            sid = session['session_id']
            scopes = active_scopes[sid] = self.chat.goal_controller.active_executions(conn, sid)
            coordinated = [goal['id'] for goal in result['goals'] if goal['leader_session_id'] == sid
                           and goal['status'] in ('running', 'pausing', 'paused', 'waiting_user')]
            active_goal_ids[sid] = list(dict.fromkeys([e['goal_id'] for e in scopes] + coordinated))
            execution = scopes[0] if scopes else None
            key = sid if self.chat.busy(sid) or execution is None else execution['id']
            active_goals[sid] = active_goal_ids[sid][0] if active_goal_ids[sid] else None
            session['web_chat'] = self.chat.path(sid).exists()
            if session['web_chat']:
                histories[sid] = self.chat.history(sid)
            session['status'] = session_presence.status(self.chat, session, present, key)
        decisions = board.decisions(conn, name, all=True)
        for decision in decisions:
            if decision['status'] == 'open':
                asker = board.session(conn, decision['session_id'])
                leader_id=trust_mode.decision_leader(conn,decision['id'])
                result['decisions'].append({**{key: decision[key] for key in (
                    'id', 'session_id', 'question', 'options', 'recommendation', 'blocking', 'created_at')},
                    'asker': asker['crew_name'], 'leader_session_id': leader_id,
                    'goal_id': links.get(('decision', decision['id']))})
        tasks = usage.dispatch_tasks(conn, None, None, project=name)
        subagent_keys, conversation_keys = {}, {}
        for row in conn.execute('SELECT e.id,e.session_id,e.goal_id,e.round,g.current_round FROM goal_executions e '
                                'JOIN goals g ON g.id=e.goal_id WHERE g.project=? ORDER BY e.generation DESC', (name,)):
            subagent_keys.setdefault(row['session_id'], []).append(row['id'])
            if row['round'] == row['current_round']:
                conversation_keys.setdefault(row['session_id'], {}).setdefault(row['goal_id'], row['id'])
        tasks_by_session = {}
        for task in tasks:
            tasks_by_session.setdefault(task['session_id'], []).append(task)
        reports, leases, claims = board.reports(conn, name), board.leases(conn, name), board.claims(conn, name)
        member_leaders = {s['session_id']: board.leader_of(conn, s['session_id'])
                          for s in sessions if s['role'] != 'leader'}
        for session in sessions:
            sid = session['session_id']
            key = sid
            binding = runtimes.get(conn, sid)
            context = self.chat.display_context(sid, conn, binding=binding)
            own_tasks = list(tasks_by_session.get(sid, []))
            scope = {identity: goal_id for goal_id, identity in conversation_keys.get(sid, {}).items()}
            scope[None] = None
            dispatches = [self._conversation_dispatch(conn, task, scope[task.get('execution_id')])
                          for task in own_tasks if task.get('source') == 'skill_dispatch' and task.get('execution_id') in scope]
            for task_key in [sid, *subagent_keys.get(sid, [])]:
                children = self.chat.subagent_tasks(task_key)
                own_tasks.extend(children)
                execution_id = None if task_key == sid else task_key
                if execution_id in scope:
                    dispatches.extend(self._conversation_dispatch(conn, task, scope[execution_id]) for task in children)
            own_tasks.sort(key=lambda task: task['started_at'])
            recent_tasks = [t for t in own_tasks if t['status'] == 'running' or
                            store.parse_iso(t.get('updated_at') or t['ended_at'] or t['started_at']) >=
                            self.now() - dt.timedelta(hours=24)]
            codex = dict(codex=[{'id': t.get('id') or t['run_id'] or t['rollout_id'], **{key: t[key] for key in
                                ('task_name', 'status', 'started_at', 'ended_at', 'duration_seconds')},
                                'source': t.get('source', 'dispatch'), 'detail': t.get('detail'),
                                'task_description': t.get('task_description'),
                                'runtime': t.get('runtime', 'codex'),
                                **({key: t.get(key) for key in ('pid', 'cwd', 'task_file', 'execution_id', 'goal_id')}
                                   if t.get('source') == 'skill_dispatch' else {})}
                               for t in recent_tasks],
                         codex_older=len(own_tasks) - len(recent_tasks), dispatches=dispatches)
            if session['role'] == 'leader':
                events = histories[sid]['events'] if sid in histories else []
                journal = self.chat.journal(sid)
                with journal.condition:
                    replies = presentation.chat_replies(journal.events,
                        cache_key=(str(journal.path), journal.signature))
                read_seq = board.read_seq(conn, sid)
                result['leaders'].append(dict(
                    session_id=sid, name=session['crew_name'], goal=session['task'], status=session['status'],
                    member_defaults=member_choices.get(sid), **looks.get(sid, {'color': None, 'avatar_version': None}),
                    active_goal_id=active_goals[sid], active_goal_ids=active_goal_ids[sid],
                    active_execution_id=active_scopes[sid][0]['id'] if active_scopes[sid] else None,
                    runtime=binding['runtime'],
                    runtime_switch=runtime_switch.state(conn,session,self.chat,agents,error),
                    trust=trust_mode.get(conn,name,sid),
                    **models.get(conn, sid), applied=self.chat.applied(key), activity=self.chat.activity(key),
                    context=context, **codex,
                    where='bg' if self.chat.running(sid) else 'vscode' if sid in general_present else 'offline',
                    busy=self.chat.busy(sid) or (sid in general_present and general_present[sid].get('status') == 'busy'),
                    running=self.chat.running(sid) or sid in general_present, web_chat=session['web_chat'], cc_name=session['cc_name'],
                    last_seq=events[-1]['seq'] if events else 0, read_seq=read_seq,
                    last_reply_id=replies[-1]['id'] if replies else None,
                    unread=sum(reply['seq'] > read_seq for reply in replies),
                    pending=sum(d['leader_session_id'] == sid for d in result['decisions']),
                    notices_pending=len(board.pending_notices(conn, sid)),
                    member_ids=[s['session_id'] for s in sessions if s['role'] != 'leader' and s['crew_name']
                                and member_leaders[s['session_id']] is not None
                                and member_leaders[s['session_id']]['session_id'] == sid]))
            elif session['crew_name'] and session['group_name'] in open_groups:
                leader = member_leaders[sid]
                card = board.card(conn, sid)
                result['members'].append(dict(
                    session_id=sid, name=session['crew_name'], title=titles.get(sid),
                    active_goal_id=active_goals[sid], active_goal_ids=active_goal_ids[sid],
                    active_execution_id=active_scopes[sid][0]['id'] if active_scopes[sid] else None,
                    execution_records=self.member_execution_records(conn, sid, active_scopes[sid]),
                    general_has_output=sid in histories and chat.has_native_output(histories[sid]['events']),
                    runtime=binding['runtime'],
                    runtime_switch=runtime_switch.state(conn,session,self.chat,agents,error),
                    **models.get(conn, sid), applied=self.chat.applied(key), activity=self.chat.activity(key),
                    context=context, busy=self.chat.busy(sid) or (sid in general_present and general_present[sid].get('status') == 'busy'),
                    running=self.chat.running(sid) or sid in general_present,
                    leader_session_id=leader['session_id'] if leader else None, group=session['group_name'],
                    status=session['status'], status_note=session['status_note'],
                    where='bg' if self.chat.running(sid) else 'vscode' if sid in general_present else 'offline',
                    **{key: session[key] for key in ('task', 'branch', 'worktree', 'cwd', 'cc_name', 'web_chat')},
                    scope=card['scope'] if card else None, done_when=card['done_when'] if card else None,
                    progress=[{'at': r['created_at'], 'text': r['body']} for r in reversed(reports) if r['session_id'] == sid][:5],
                    **codex,
                    leases=[{key: lease[key] for key in ('resource', 'purpose', 'taken_at', 'until_at')}
                            for lease in leases if lease['holder_session'] == sid],
                    claims=[{key: claim[key] for key in ('pattern', 'note')} for claim in claims if claim['session_id'] == sid]))
        leader_cards = result['leaders']
        builtin_ids = {session['session_id'] for session in sessions if git_admin.is_builtin(session, git_config)}
        result['leaders'] = board.sort_leaders(conn, name, [leader for leader in leader_cards
                                                         if leader['session_id'] not in builtin_ids])
        pinned = board.pinned_leaders(conn, name)
        for leader in leader_cards:
            sid = leader['session_id']
            leader['pinned'] = sid in pinned
            if git_config is not None and git_config['admin_session_id'] == sid:
                result['git_assistant'] = git_admin.assistant_state(
                    conn, git_config, leader, decisions, builtin=sid in builtin_ids)
        result['git_branches'] = self._git_branches(conn, status['project'], git_config)
        result['candidates'] = self.candidates(conn, name)
        result['domains'] = domains.summary(conn, name)
        from .. import decision_briefs, decision_flow
        result['decisions'] = decision_flow.enrich(conn, decision_briefs.enrich(conn, result['decisions']))
        result['attention'] = presentation.attention(conn, sessions, decisions, tasks,
            {sid: history['events'] for sid, history in histories.items()})
        return result, histories

    def managed_agents(self, conn, agents):
        scoped_native = self.chat.goal_controller.native_ids(conn)
        agents = session_presence.external_agents(self.chat, agents)
        result = {a['sessionId']: dict(a) for a in agents if a['sessionId'] not in scoped_native}
        with self.chat.lock:
            for sid, current in self.chat.processes.items():
                if current.process.poll() is None:
                    logical_id = current.execution['session_id'] if current.execution else sid
                    session = board.session(conn, logical_id)
                    busy = self.chat.busy(sid) or result.get(logical_id, {}).get('status') == 'busy'
                    result[logical_id] = {'sessionId': logical_id, 'name': session['crew_name'], 'pid': current.process.pid,
                                   'cwd': session['cwd'], 'kind': 'crew',
                                   'status': 'busy' if busy else 'idle'}
        return list(result.values())

    def limits(self, conn):
        latest = conn.execute('SELECT * FROM codex_rate_limits ORDER BY ts DESC LIMIT 1').fetchone()
        codex = None
        if (latest is not None and latest['used_percent'] is not None and latest['resets_at'] is not None
                and store.parse_iso(latest['resets_at']) > self.now()):
            codex = dict(used_percent=latest['used_percent'], resets_at=latest['resets_at'], captured_at=latest['ts'], expired=False)
        return {'claude': self._claude_limits(),
                'codex': codex}

    def _claude_limits(self):
        limits = claude_limits.read(self.home, cache_path=self.claude_root.parent.parent / '.claude.json', now=self.now())
        seven_day = limits['seven_day']
        reset, crew = self.crew_usage
        # A new or expired account window must not inherit the previous week's estimate.
        if (seven_day is not None and seven_day['used_percent'] is not None
                and crew is not None and seven_day['resets_at'] == reset):
            seven_day['crew'] = crew
        limits['budget'] = self.claude_budget
        return limits

    def _budget(self, conn, now):
        """The API dollar budget for display; a broken settings file shows its error instead of failing the page."""
        try:
            return claude_budget.read(self.home, conn, now)
        except (ValueError, OSError) as exc:
            return {'error': f'Claude 额度设置无法读取：{exc}'}

    def decision_participants(self, conn, decision):
        asker = board.session(conn, decision['session_id'])
        leader_id=trust_mode.decision_leader(conn,decision['id'])
        leader=board.session(conn,leader_id) if leader_id is not None else None
        if leader is not None and (leader['project']!=decision['project'] or leader['role']!='leader'
                                  or leader['status']=='closed' or board.is_archived(conn,leader_id)):
            leader=None
        with self.agents_lock:
            if self.agents_error is None:
                live = board.live_sessions(self.agents)
                if asker['session_id'] in live:
                    asker['cc_name'] = live[asker['session_id']]['name']
        return asker, leader
