"""Workspace read projection, independent of HTTP and background worker ownership.

Inputs are observation/query ports. This module never starts, stops or queues a
conversation; refreshing external caches belongs to its application caller.
"""
from contextlib import closing
from dataclasses import dataclass
import datetime as dt

from . import board, channel, goal_api, goals, member_defaults, models, presentation, runtimes, session_presence, settings, store, themes, todo, trust_mode, usage


@dataclass
class Snapshot:
    home: object
    executions: object
    active_executions: object
    runtime_state: object
    clock: object
    select_project: object
    runtime_snapshot: object
    notification_status: object
    limits: object
    native_approvals: object
    candidates: object
    title_contexts: object
    managed_agents: object
    channel_digest_state: object

    def read(self, project_name, observation, *, vscode_error=None):
        agents, error = observation.agents, observation.error
        general_present = board.live_sessions(agents)
        native_catalog = self.runtime_snapshot()
        claude_catalog = next((item for item in native_catalog if item['id'] == 'claude'), {})
        with closing(store.connect(self.home)) as conn:
            result = {'project': None, 'projects': [{key: p[key] for key in ('name', 'display_name', 'theme')} for p in board.projects(conn)],
                      'themes': themes.THEMES,
                      'models': claude_catalog.get('models', []), 'efforts': claude_catalog.get('efforts', []),
                      'runtimes': native_catalog,
                      'ai': settings.load(conn),
                      'now': self.clock().isoformat(), 'agents_error': error, 'notification': self.notification_status(),
                      'leaders': [], 'members': [], 'decisions': [], 'attention': [],
                      'todos': [], 'todo_count': 0, 'goals': [], 'native_approvals': [],
                      'candidates': {'running': [], 'others': []},
                      'channel': {'unread': 0, 'latest_id': 0, 'digesting': False, 'digest_error': None, 'digest_id': None},
                      'limits': self.limits(conn)}
            result['vscode_error'] = vscode_error
            if project_name is not None:
                name = self.select_project(conn, project_name)
                result['goals'] = [goal_api.card(conn, goal) for goal in goals.list_goals(conn, name)]
                card = channel.digest(conn, name)
                result['channel'] = {**channel.state(conn, name), **self.channel_digest_state(name),
                                     'digest_id': card['id'] if card is not None else None}
                status, present, agents = session_presence.project_status(self.managed_agents, conn, name, agents, error)
                git_config = board.git_config(conn, name)
                result['project'] = {key: status['project'][key] for key in ('name', 'display_name', 'root', 'theme')}
                result['todos'] = todo.list(conn, name)
                links = {(row['kind'], row['item_id']): row['goal_id'] for row in conn.execute(
                    'SELECT l.kind,l.item_id,l.goal_id FROM goal_links l JOIN goals g ON g.id=l.goal_id WHERE g.project=?', (name,))}
                for item in result['todos']:
                    item['goal_id'] = links.get(('todo', item['id']))
                result['todo_count'] = len(result['todos'])
                open_groups = {group['name'] for group in status['groups'] if group['closed_at'] is None}
                removed = {r[0] for r in conn.execute('SELECT session_id FROM session_controls WHERE removed=1')}
                sessions = [s for s in [*status['ungrouped'], *(s for group in status['groups'] for s in group['members'])]
                            if s['session_id'] not in removed and s['status'] != 'closed']
                member_choices = member_defaults.for_project(conn, name)
                titles, contexts = self.title_contexts(sessions)
                result['native_approvals'] = self.native_approvals(name)
                histories, active_scopes, active_goals, active_goal_ids = {}, {}, {}, {}
                for session in sessions:
                    sid = session['session_id']
                    scopes = active_scopes[sid] = self.active_executions(conn, sid)
                    coordinated = [goal['id'] for goal in result['goals'] if goal['leader_session_id'] == sid
                                   and goal['status'] in ('running', 'pausing', 'paused', 'waiting_user')]
                    active_goal_ids[sid] = list(dict.fromkeys([e['goal_id'] for e in scopes] + coordinated))
                    execution = scopes[0] if scopes else None
                    key = sid if self.executions.busy(sid) or execution is None else execution['id']
                    active_goals[sid] = active_goal_ids[sid][0] if active_goal_ids[sid] else None
                    session['web_chat'] = self.executions.path(sid).exists()
                    if session['web_chat']:
                        histories[sid] = self.executions.history(sid)
                    session['status'] = session_presence.status(self.executions, session, present, key)
                decisions = board.decisions(conn, name, all=True)
                for decision in decisions:
                    if decision['status'] == 'open':
                        asker = board.session(conn, decision['session_id'])
                        leader_id=trust_mode.decision_leader(conn,decision['id'])
                        result['decisions'].append({**{key: decision[key] for key in (
                            'id', 'session_id', 'question', 'options', 'recommendation', 'blocking', 'created_at', 'held', 'held_at')},
                            'asker': asker['crew_name'], 'leader_session_id': leader_id,
                            'goal_id': links.get(('decision', decision['id']))})
                tasks = usage.dispatch_tasks(conn, None, None, project=name)
                subagent_keys = {}
                for row in conn.execute('SELECT e.id,e.session_id FROM goal_executions e '
                                        'JOIN goals g ON g.id=e.goal_id WHERE g.project=?', (name,)):
                    subagent_keys.setdefault(row['session_id'], []).append(row['id'])
                reports, leases, claims = board.reports(conn, name), board.leases(conn, name), board.claims(conn, name)
                member_leaders = {s['session_id']: board.leader_of(conn, s['session_id'])
                                  for s in sessions if s['role'] != 'leader'}
                for session in sessions:
                    sid = session['session_id']
                    key = sid
                    context = self.executions.context(key) if self.executions.running(key) else contexts.get(sid)
                    own_tasks = [t for t in tasks if t['session_id'] == sid]
                    for task_key in [sid, *subagent_keys.get(sid, [])]:
                        own_tasks.extend(self.executions.subagent_tasks(task_key))
                    own_tasks.sort(key=lambda task: task['started_at'])
                    recent_tasks = [t for t in own_tasks if t['status'] == 'running' or
                                    store.parse_iso(t.get('updated_at') or t['ended_at'] or t['started_at']) >=
                                    self.clock() - dt.timedelta(hours=24)]
                    codex = dict(codex=[{'id': t.get('id') or t['run_id'] or t['rollout_id'], **{key: t[key] for key in
                                        ('task_name', 'status', 'started_at', 'ended_at', 'duration_seconds')},
                                        'source': t.get('source', 'dispatch'), 'detail': t.get('detail'),
                                        'task_description': t.get('task_description'),
                                        'runtime': t.get('runtime', 'codex'),
                                        **({key: t.get(key) for key in ('pid', 'cwd', 'task_file', 'execution_id', 'goal_id')}
                                           if t.get('source') == 'skill_dispatch' else {})}
                                       for t in recent_tasks],
                                 codex_older=len(own_tasks) - len(recent_tasks))
                    if session['role'] == 'leader':
                        events = histories[sid]['events'] if sid in histories else []
                        replies = presentation.chat_replies(events)
                        read_seq = board.read_seq(conn, sid)
                        result['leaders'].append(dict(
                            session_id=sid, name=session['crew_name'], goal=session['task'], status=session['status'],
                            member_defaults=member_choices.get(sid),
                            active_goal_id=active_goals[sid], active_goal_ids=active_goal_ids[sid],
                            active_execution_id=active_scopes[sid][0]['id'] if active_scopes[sid] else None,
                            runtime=runtimes.get(conn, sid)['runtime'],
                            runtime_switch=self.runtime_state(conn, session, agents, error),
                            trust=trust_mode.get(conn,name,sid),
                            is_git_admin=git_config is not None and git_config['admin_session_id'] == sid,
                            **models.get(conn, sid), applied=self.executions.applied(key), activity=self.executions.activity(key),
                            context=context, **codex,
                            where='bg' if self.executions.running(sid) else 'vscode' if sid in general_present else 'offline',
                            busy=self.executions.busy(sid) or (sid in general_present and general_present[sid].get('status') == 'busy'),
                            running=self.executions.running(sid) or sid in general_present, web_chat=session['web_chat'], cc_name=session['cc_name'],
                            last_seq=events[-1]['seq'] if events else 0, read_seq=read_seq,
                            last_reply_id=replies[-1]['id'] if replies else None,
                            unread=sum(reply['seq'] > read_seq for reply in replies),
                            pending=sum(d['leader_session_id'] == sid and not d['held'] for d in result['decisions']),
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
                            runtime=runtimes.get(conn, sid)['runtime'],
                            runtime_switch=self.runtime_state(conn, session, agents, error),
                            **models.get(conn, sid), applied=self.executions.applied(key), activity=self.executions.activity(key),
                            context=context, busy=self.executions.busy(sid) or (sid in general_present and general_present[sid].get('status') == 'busy'),
                            running=self.executions.running(sid) or sid in general_present,
                            leader_session_id=leader['session_id'] if leader else None, group=session['group_name'],
                            status=session['status'], status_note=session['status_note'],
                            where='bg' if self.executions.running(sid) else 'vscode' if sid in general_present else 'offline',
                            **{key: session[key] for key in ('task', 'branch', 'worktree', 'cwd', 'cc_name', 'web_chat')},
                            scope=card['scope'] if card else None, done_when=card['done_when'] if card else None,
                            progress=[{'at': r['created_at'], 'text': r['body']} for r in reversed(reports) if r['session_id'] == sid][:5],
                            **codex,
                            leases=[{key: lease[key] for key in ('resource', 'purpose', 'taken_at', 'until_at')}
                                    for lease in leases if lease['holder_session'] == sid],
                            claims=[{key: claim[key] for key in ('pattern', 'note')} for claim in claims if claim['session_id'] == sid]))
                result['leaders'] = board.sort_leaders(conn, name, result['leaders'])
                priorities = board.leader_priorities(conn, name, result['leaders'])
                for leader in result['leaders']:
                    leader['priority'] = priorities.get(leader['session_id'])
                result['candidates'] = self.candidates(conn, name)
                from . import decision_briefs, decision_flow
                result['decisions'] = decision_flow.enrich(conn, decision_briefs.enrich(conn, result['decisions']))
                result['attention'] = presentation.attention(conn, sessions, decisions, tasks,
                    {sid: history['events'] for sid, history in histories.items()})
                for item in result['attention']:
                    if item['kind'] == 'codex_failed' and item.get('task_id') is not None:
                        item['codex_task_id'] = item['task_id']
                    identity = links.get(('decision', item.get('decision_id'))) if item['kind'] == 'decision' else None
                    item['goal_id'] = identity
                    item['task_id'] = identity if item['kind'] == 'decision' else item.get('task_id')
            return result

