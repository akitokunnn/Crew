"""Goal execution routing: stable people, separate native histories, no extra polling."""
from contextlib import closing
import json
import os
from pathlib import Path
from . import behavior_prompts
import subprocess
import threading
import uuid

from . import board, goal_context, goals, install, messaging, models, native_handoff, report_preview, runtimes, spawn, store, trust_mode


def protocol(conn, execution):
    """Editable goal protocol for the session that owns this execution."""
    return behavior_prompts.text(conn, 'goal_protocol', session_id=execution['session_id'])


class Controller:
    def __init__(self, manager):
        self.manager = manager
        self.home = manager.home
        self.recovery = None
        self.epoch = uuid.uuid4().hex
        self.live_native = None
        self.recovery_errors = {}

    def agent_action(self, action, data, actor_session_id, execution_id=None):
        from . import goal_actions
        if action == 'report.preview':
            # Waiting for the desktop capture must not pause other native work.
            return goal_actions.dispatch(self.home, actor_session_id, execution_id, action, data, controller=self)
        with self.manager.lock:
            return goal_actions.dispatch(self.home, actor_session_id, execution_id, action, data, controller=self)

    def current(self, conn, goal_id, session_id):
        goal = goals.get(conn, goal_id)
        goals._member(conn, goal, session_id)
        rows = conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND round=? AND session_id=? '
                            'ORDER BY generation DESC',
                            (goal_id, goal['current_round'], session_id)).fetchall()
        return None if not rows else goals.get_execution(conn, rows[0]['id'])

    def active_executions(self, conn, session_id):
        """Current task scopes for display; never infer a message destination."""
        rows = conn.execute("SELECT e.id FROM goal_executions e JOIN goals g ON g.id=e.goal_id "
            "WHERE e.session_id=? AND e.round=g.current_round "
            "AND g.status IN ('queued','running','pausing','paused','waiting_user') "
            "AND e.status NOT IN ('completed','failed','cancelled') "
            "AND e.generation=(SELECT MAX(x.generation) FROM goal_executions x WHERE x.goal_id=e.goal_id "
            "AND x.round=e.round AND x.session_id=e.session_id) "
            "ORDER BY CASE WHEN g.status='running' THEN 0 ELSE 1 END,g.updated_at DESC,e.created_at DESC",
            (session_id,)).fetchall()
        values = [goals.get_execution(conn, row['id']) for row in rows]
        return sorted(values, key=lambda item: (not self.manager.busy(item['id']), not self.manager.running(item['id'])))

    def display_execution(self, conn, session_id):
        """Representative activity only; general inputs always keep their own scope."""
        values = self.active_executions(conn, session_id)
        return values[0] if values else None

    def general_context(self, conn, session_id):
        rows = conn.execute('SELECT DISTINCT g.id FROM goals g LEFT JOIN goal_assignments a ON a.goal_id=g.id '
            'WHERE g.leader_session_id=? OR a.session_id=? ORDER BY g.updated_at DESC LIMIT 3',
            (session_id, session_id)).fetchall()
        if not rows:
            return ''
        lines = ['[Crew 目标索引；原生历史分别保存。需要详情用 crew goal status 查阅，不另起业务方案；暂停/结束不代表获准复工。]']
        for row in rows:
            value = goals.get(conn, row['id'])
            lines.append('%s | %s | %s' % (value['id'], value['title'][:60], value['status']))
        return '\n'.join(lines)

    def ensure(self, conn, goal_id, session_id):
        goal = goals.get(conn, goal_id)
        gate = goals.dispatch_check(conn, goal_id)
        if not gate['allowed']:
            raise board.Conflict(gate['reason'])
        execution = self.current(conn, goal_id, session_id)
        if execution is not None and execution['status'] not in goals.TERMINAL:
            native_handoff.assert_control(conn, session_id, execution['id'])
            mapping, options = runtimes.get(conn, session_id), models.get(conn, session_id)
            if execution['runtime'] != mapping['runtime']:
                if self.manager.busy(execution['id']):
                    raise board.Conflict('更换载体前请先暂停当前目标')
                if self.manager.running(execution['id']):
                    self.manager.stop_managed(execution['id'])
                goals.configure_execution(conn, execution['id'], runtime=mapping['runtime'], **options)
                execution = goals.get_execution(conn, execution['id'])
            if execution['status'] in ('offline', 'paused'):
                goals.set_execution_status(conn, execution['id'], 'queued')
                execution = goals.get_execution(conn, execution['id'])
            return execution
        if not any(item['session_id'] == session_id and item['status'] == 'assigned' for item in goal['assignments']):
            goals.assign(conn, goal_id, session_id, '处理用户在此目标内的输入')
        mapping, options = runtimes.get(conn, session_id), models.get(conn, session_id)
        return goals.create_execution(conn, goal_id, session_id, runtime=mapping['runtime'],
                                      model=options['model'], effort=options['effort'])

    def ensure_discussion(self, conn, goal_id, session_id, identity):
        current = self.current(conn, goal_id, session_id)
        if current is not None and goals.discussion(conn, current['id']) and current['status'] in ('queued', 'running', 'idle', 'offline'):
            gate = goals.dispatch_check(conn, goal_id, execution_id=current['id'], allow_discussion=True)
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
            conn.execute('UPDATE goal_discussion_executions SET message_id=? WHERE execution_id=?', (identity, current['id']))
            return current
        self.stop_delivered(conn, goal_id)
        mapping, options = runtimes.get(conn, session_id), models.get(conn, session_id)
        return goals.create_execution(conn, goal_id, session_id, runtime=mapping['runtime'],
                                      model=options['model'], effort=options['effort'], discussion_message_id=identity)

    def target(self, conn, execution):
        return {**board.session(conn, execution['session_id']), '_storage_id': execution['id'],
                '_execution': execution}

    def human_handoff(self, conn, execution, identity):
        if conn.execute('SELECT 1 FROM goal_context_applied WHERE execution_id=? LIMIT 1', (execution['id'],)).fetchone():
            return []
        requirement = goals.requirements(conn, execution['goal_id'])['request']
        material, seen = [], set()
        for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND round<=? AND session_id=? ORDER BY round,generation',
                                (execution['goal_id'], execution['round'], execution['session_id'])):
            for event in self.manager.journal(row['id']).events:
                if event['type'] != 'message' or event.get('source') not in ('human', 'qq', 'lark') or event['id'] == identity:
                    continue
                if event['id'] in seen:
                    continue
                seen.add(event['id'])
                if event.get('text') and event['text'] in requirement:
                    continue
                material.append({'source': {'type': 'human-message', 'id': row['id'] + ':' + event['id']},
                    'text': '用户补充原文（%s，当前需求版本优先；不得遗失其中仍有效的约束）：\n%s' % (event['at'], event['text']),
                    'reason': '换代或切载体时保留用户精确原话；普通轮次不重发', 'protected': True})
        return material

    def lost_native_handoff(self, conn, execution):
        """Hand a new generation what Crew recorded of a predecessor whose Claude conversation is gone.

        Returns (replacing, material); empty once this generation has applied
        context or produced native output of its own.
        """
        from . import chat, runtime_switch
        if (conn.execute('SELECT 1 FROM goal_context_applied WHERE execution_id=? LIMIT 1', (execution['id'],)).fetchone()
                or chat.has_native_output(self.manager.journal(execution['id']).events)):
            return None, []
        row = conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=? AND id<>? '
                           'AND (round<? OR round=? AND generation<?) ORDER BY round DESC,generation DESC LIMIT 1',
                           (execution['goal_id'], execution['session_id'], execution['id'],
                            execution['round'], execution['round'], execution['generation'])).fetchone()
        if row is None:
            return None, []
        previous = goals.get_execution(conn, row['id'])
        lost = self.manager.missing_native(conn, self.target(conn, previous))
        if lost is None:
            return None, []
        events = self.manager.journal(previous['id']).events
        recent = '\n\n'.join(reversed(runtime_switch._texts(reversed(events))))[-runtime_switch.MAX_HANDOFF:]
        last = next((text for text in ('\n'.join(block['text'] for block in event['content'] if block.get('type') == 'text').strip()
                                       for event in reversed(events) if event['type'] == 'assistant') if text), '（没有记录到文字结果）')
        text = ('原执行会话丢失交接：上一代执行 %s（第 %s 轮第 %s 代）的 Claude 原生会话 %s 已丢失。'
                '本执行是 Crew 新开的会话，不是接回原会话；原会话里的上下文无法接回。'
                '以下是 Crew 记录里该执行的最后结果和最近片段，仅为历史，先核对实际代码与证据，已完成的不重做。\n'
                '最后结果：%s\n\n最近片段：\n%s' % (previous['id'], previous['round'], previous['generation'], lost,
                                                 last[-runtime_switch.MAX_HANDOFF:], recent))
        return ({'lost_native': lost, 'lost_execution': previous['id']},
                [{'source': {'type': 'lost-native', 'id': previous['id']}, 'text': text,
                  'reason': '原会话丢失后新开执行，交接原执行的记录摘要与最后结果', 'protected': True}])

    def replace_lost(self, execution_id, lost, text, *, identity, display_text):
        """End a generation whose Claude conversation is gone; the next generation takes over with a handoff."""
        from . import chat
        with self.manager.lock, closing(store.connect(self.home)) as conn:
            execution = goals.get_execution(conn, execution_id)
            if execution['status'] not in goals.TERMINAL:
                goals.set_execution_status(conn, execution_id, 'failed', error=chat.NATIVE_MISSING % lost)
        return self.send(execution['goal_id'], execution['session_id'], text, identity=identity,
                         source='crew', display_text=display_text)

    def followup_context(self, conn, execution):
        """Continue the latest delivery, within this task or an explicit new round."""
        row = conn.execute('SELECT id FROM goal_reports WHERE goal_id=? AND (round<? OR (round=? AND requirement_version<?)) '
                           'ORDER BY round DESC,version DESC LIMIT 1',
                           (execution['goal_id'], execution['round'], execution['round'], execution['requirement_version'] +
                            (1 if goals.discussion(conn, execution['id']) else 0))).fetchone()
        if row is None:
            return []
        report = goals.get_report(conn, row['id'])
        data = report['data']
        review = goals.report_review(conn, execution['goal_id'], report['id'])
        text = '此前交付（历史证据，不代表当前修改已完成）：报告 %s，记录轮次 %s。\n%s' % (
            report['id'], report['round'], json.dumps({key: data[key] for key in
                ('summary', 'delivered', 'unresolved', 'artifacts', 'follow_up')}, ensure_ascii=False))
        if review is not None:
            text += '\n用户对此前交付的验收：%s。原话：%s' % ('通过' if review['accepted'] else '需要修改', review['note'])
        text += '\n沿用已有结果处理用户的新输入，不重复施工；完整报告可用 crew goal status 查阅。'
        text += '\n普通讨论无需新增工作清单或提交报告。确有修改时先 amend 登记，再用 work.list 继承已有工作；只重开实际受影响的旧项或补充新项，按需复核，不把此前交付当作新改动已完成。'
        return [{'source': {'type': 'goal-report', 'id': report['id']}, 'text': text,
                 'reason': '继续本任务时保留此前交付与用户修改意见', 'protected': True}]

    def stop_delivered(self, conn, goal_id):
        """Drain delivered runtimes before a human review or follow-up round."""
        manager, idle, agents = self.manager, [], None
        for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (goal_id,)):
            key = row['id']
            current = manager.processes.get(key)
            if manager.running(key):
                if manager.busy(key):
                    raise board.Conflict('原生执行仍在工作，尚不能验收或继续修改')
                idle.append(key)
            elif current is not None:
                if not current.reader_done.is_set():
                    raise board.Conflict('原生执行输出尚未排空，尚不能验收或继续修改')
            else:
                execution = goals.get_execution(conn, key)
                if execution['runtime'] == 'claude':
                    identities = {b['native_id'] for b in execution['bindings'] if b['runtime'] == 'claude'}
                    if identities:
                        if agents is None:
                            agents = manager._external_agents()
                        if any(agent['sessionId'] in identities for agent in agents):
                            raise board.Conflict('原生执行仍在外部运行，尚不能验收或继续修改')
                else:
                    from .codex_runtime import RuntimeLease
                    lease = RuntimeLease(manager.home, key).acquire()
                    lease.release()
        for key in idle:
            manager.stop_managed(key)
        self.sync(conn, goal_id)

    def launch(self, session, spec):
        """Called under Manager.lock; never mutates the person's default native binding."""
        return self._launch(session, spec, None)

    def check_member_start(self, conn, goal_id, session_id, source):
        """A second dispatch must not turn an already working member into two workers.

        Independent goals and explicit human conversations retain their own histories.
        Existing goal histories may recover; this guards the first dispatch from a
        general conversation into a newly assigned goal.
        """
        person = board.session(conn, session_id)
        if source in ('human', 'qq', 'lark') or person['role'] != 'member':
            return
        prior = conn.execute('SELECT 1 FROM goal_executions e JOIN goal_execution_bindings b ON b.execution_id=e.id '
                             'WHERE e.goal_id=? AND e.session_id=? LIMIT 1',
                             (goal_id, session_id)).fetchone()
        if prior is None and self.manager.scope_busy(session_id, **runtimes.get(conn, session_id)):
            raise board.Conflict('成员「%s」的总对话正在执行任务，不能再新开目标线程重复派工；'
                '请等待该轮结束或明确停止后再分配。新成员直接用 crew spawn --goal %s 绑定目标启动。'
                % (person['crew_name'], goal_id))

    def launch_maintenance(self, session, spec, marker):
        """Resume an existing goal history only so maintenance can send the native compact command.

        Goal dispatch is not consulted because the goal is not being worked on;
        without an existing native binding nothing starts.
        """
        execution = session['_execution']
        if not any(item['runtime'] == execution['runtime'] and item['native_id'] for item in execution['bindings']):
            raise board.BoardError('目标执行没有原生会话，压缩不会新建会话')
        return self._launch(session, spec, marker)

    def _launch(self, session, spec, maintenance):
        from . import chat
        execution = session['_execution']
        key, logical_id = execution['id'], execution['session_id']
        previous = self.manager.processes.get(key)
        if previous is not None and not previous.reader_done.wait(10):
            raise board.BoardError('上一轮目标执行的输出尚未排空，请稍后重试；未启动新进程')
        with closing(store.connect(self.home)) as conn:
            native_handoff.assert_control(conn, logical_id, key, spec.get('_native_control_token'))
            if maintenance is None and not spec.get('_native_control_token'):
                gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=key, allow_discussion=True)
                if not gate['allowed']:
                    raise board.Conflict(gate['reason'])
            from . import settings
            permission_mode = settings.get_execution(conn)['claude']
        options = {'model': execution['model'], 'effort': execution['effort']}
        binding = next((item for item in execution['bindings'] if item['runtime'] == execution['runtime']), None)
        if execution['runtime'] == 'claude' and binding is not None:
            if self.live_native is None:
                raise board.Conflict('Claude 进程状态尚未确认，不能重复恢复目标执行')
            if binding['native_id'] in self.live_native:
                raise board.Conflict('目标原生会话仍在外部运行，等待它退出后续接')
        journal = self.manager.journal(key)
        if execution['runtime'] == 'codex':
            from .codex_runtime import CodexRunning
            current = CodexRunning(self.home, self.manager.plugin_root, session, spec, options, journal,
                                   self.manager.send_message, process_factory=self.manager.process_factory,
                                   resume=binding is not None, execution=execution, goal_handler=self.agent_action,
                                   maintenance=maintenance)
            self.manager.processes[key] = current
            return spawn.command(key, session['crew_name'], session['role'], runtime='codex', **options)
        native_id = binding['native_id'] if binding else str(uuid.uuid4())
        if binding is None:
            with closing(store.connect(self.home)) as conn:
                goals.bind_native(conn, key, 'claude', native_id, **options)
        install.ensure(self.home, self.manager.plugin_root)
        with closing(store.connect(self.home)) as prompt_conn:
            identity_text = behavior_prompts.text(prompt_conn, 'execution_identity', session_id=logical_id,
                values={'crew_name': session['crew_name'], 'session_id': logical_id}, plugin_root=self.manager.plugin_root)
        command = spawn.command(native_id, session['crew_name'], session['role'], binding is not None,
                                instructions=(identity_text
                                              + ('\n' + spec['_workspace_notice'] if spec.get('_workspace_notice') else '')),
                                permission_mode=permission_mode, plugin_root=self.manager.plugin_root,
                                preview_directory=report_preview.image_directory(self.home, logical_id, create=True),
                                **options)
        with closing(store.connect(self.home)) as prompt_conn:
            prompt_manifest = behavior_prompts.for_session(prompt_conn, logical_id, plugin_root=self.manager.plugin_root)
        command = behavior_prompts.append_to_command(command, behavior_prompts.render(prompt_manifest))
        command[0] = chat.claude_executable()
        environment = chat.child_environment({**spec, 'name': session['crew_name'], 'role': session['role'],
                                               'group': session['group_name']})
        environment.update(CREW_HOME=str(self.home.resolve()), CREW_SESSION_ID=logical_id,
                           CREW_EXECUTION_ID=key, CREW_GOAL_ID=execution['goal_id'])
        environment['PATH'] = str(self.home.resolve() / 'bin') + os.pathsep + environment.get('PATH', '')
        process = self.manager.process_factory(command, cwd=session['cwd'], env=environment,
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, encoding='utf-8', bufsize=1,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        current = chat.Running(process, journal)
        current.resumed_native = native_id if binding is not None else None
        current.replacing = session.get('_native_replacing')
        current.behavior_prompt_hash = behavior_prompts.manifest_hash(prompt_manifest)
        with closing(store.connect(self.home)) as prompt_conn:
            behavior_prompts.record_applied(prompt_conn, key, prompt_manifest, status='sent',
                evidence='Claude 目标进程创建成功；启动参数已携带 Crew 配置，原生读取尚未回报')
        current.execution, current.identity_session_id, current.native_id = execution, logical_id, native_id
        current.maintenance = maintenance
        current.goal_manifests = dict(session.get('_goal_manifests', {}))
        self.manager.processes[key] = current
        journal.reset_runtime()
        self.manager._control(current, 'get_settings')
        self.manager._control(current, 'get_context_usage')
        current.stderr_reader = threading.Thread(target=self.manager._stderr, args=(current,), daemon=True, name='crew-goal-stderr')
        current.stderr_reader.start()
        current.reader = threading.Thread(target=self.manager._read, args=(key, current), daemon=True, name='crew-goal-claude')
        current.reader.start()
        return command

    def send(self, goal_id, session_id, text, *, identity=None, images=None, source='human', display_text=None, sender=None):
        with self.manager.work():
            return self._send(goal_id, session_id, text, identity=identity, images=images, source=source, display_text=display_text, sender=sender)

    def _send(self, goal_id, session_id, text, *, identity=None, images=None, source='human', display_text=None, sender=None):
        identity = str(uuid.uuid4()) if identity is None else identity
        with self.manager.lock, closing(store.connect(self.home)) as conn:
            goal = goals.get(conn, goal_id)
            goals._member(conn, goal, session_id)
            for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=?', (goal_id, session_id)):
                native_handoff.assert_control(conn, session_id, row['id'])
            if self.manager.maintenance_held(session_id):
                # Refuse before any goal or execution state changes.
                from . import chat
                raise board.Conflict(chat.MAINTENANCE_HELD)
            human = source in ('human', 'qq', 'lark')
            response_only = human and goal['status'] in ('completed', 'failed', 'awaiting_acceptance')
            if human:
                # Retry the original receipt even if a later report closed its
                # round. A transport retry is never a new continuation request.
                for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=? ORDER BY round DESC,generation DESC',
                                        (goal_id, session_id)):
                    prior_execution = goals.get_execution(conn, row['id'])
                    previous = self.manager.journal(row['id']).inputs.get(identity)
                    if previous is not None and previous['status'] != 'rejected':
                        result = self.manager.send(self.target(conn, prior_execution), text, identity=identity,
                            images=images, source=source, display_text=display_text, sender=sender)
                        return {**result, 'goal_id': goal_id, 'execution_id': row['id']}
            self.check_member_start(conn, goal_id, session_id, source)
            if messaging.stopped(conn, session_id):
                if not human:
                    raise board.Conflict('用户已停止此会话；排队消息不能自动恢复它')
                if goal['manual_pause'] or goal['status'] in ('pausing', 'paused'):
                    raise board.Conflict('目标已手动暂停，请在目标中明确恢复')
                messaging.clear_stop(conn, session_id)
            if goal['status'] == 'waiting_user' and source in ('human', 'qq', 'lark') and not goal['manual_pause']:
                goal = goals.resume(conn, goal_id, actor=board.HUMAN)
                finish = goals.get_finish(conn, goal_id)
                if finish and finish['status'] == 'pending':
                    goal = goals.amend(conn, goal_id, text, actor=board.HUMAN)
            if goal['status'] == 'queued':
                goals.transition(conn, goal_id, 'running')
            execution = (self.ensure_discussion(conn, goal_id, session_id, identity) if response_only
                         else self.ensure(conn, goal_id, session_id))
            key = execution['id']
            journal = self.manager.journal(key)
            if not journal.path.exists():
                journal.set_status('idle')
            target = self.target(conn, execution)
            prior = journal.inputs.get(identity)
            if prior is not None and prior['status'] != 'rejected':
                result = self.manager.send(target, text, identity=identity, images=images, source=source,
                                           display_text=display_text, sender=sender)
                return {**result, 'goal_id': goal_id, 'execution_id': key}
            observed = self.manager.context(key)
            history = {'known': observed is not None, 'tokens': observed['used'] if observed else None,
                       'source': 'native_context_usage', 'note': '原生历史未知时不当作零'}
            replacing, lost = self.lost_native_handoff(conn, execution)
            prepared = goal_context.build(conn, key, base=protocol(conn, execution),
                                          latest_input='' if text == goal['requirements']['request'] else text,
                                          native_history=history, retrieval=self.human_handoff(conn, execution, identity) + self.followup_context(conn, execution) + lost,
                                          limits={'native_history_tokens': 64000})
            if prepared['manifest']['rotation_required'] and not self.manager.busy(key):
                if self.manager.running(key):
                    self.manager.stop_managed(key)
                goals.set_execution_status(conn, key, 'completed')
                execution = (self.ensure_discussion(conn, goal_id, session_id, identity) if response_only
                             else self.ensure(conn, goal_id, session_id))
                key, target = execution['id'], self.target(conn, execution)
                journal = self.manager.journal(key)
                if not journal.path.exists():
                    journal.set_status('idle')
                replacing, lost = self.lost_native_handoff(conn, execution)
                prepared = goal_context.build(conn, key, base=protocol(conn, execution),
                                              latest_input='' if text == goal['requirements']['request'] else text,
                                              native_history={'known': True, 'tokens': 0,
                                              'source': 'fresh_native_generation'}, limits={'native_history_tokens': 64000},
                                              retrieval=self.human_handoff(conn, execution, identity) + self.followup_context(conn, execution) + lost)
            current = self.manager.processes.get(key)
            if current is not None:
                current.pause_interrupt = False
                current.goal_manifests[identity] = prepared['manifest']['id']
            target['_goal_manifests'] = {identity: prepared['manifest']['id']}
            if replacing is not None:
                target['_native_replacing'] = replacing
            goals.set_execution_status(conn, key, 'running')
            result = self.manager.send(target, prepared['text'], display_text=text if display_text is None else display_text,
                                       identity=identity, images=images, source=source, sender=sender)
            if execution['runtime'] == 'codex' and not result.get('duplicate'):
                goal_context.mark_applied(conn, prepared['manifest']['id'], execution_id=key)
            return {**result, 'goal_id': goal_id, 'execution_id': key}

    def history(self, conn, goal_id, session_id, *, include_previous=True):
        execution = self.current(conn, goal_id, session_id)
        value = ({'events': [], 'busy': False, 'running': False, 'context': None, 'compaction': None}
                 if execution is None else self.manager.history(execution['id']))
        if execution is not None:
            value.update(self.manager.context_state(execution['id']))
        value.update(session_id=session_id, goal_id=goal_id,
                     execution_id=execution['id'] if execution else None,
                     response_mode='discussion' if execution and goals.discussion(conn, execution['id']) else 'work',
                     read_seq=execution.get('read_seq', 0) if execution else 0)
        if include_previous:
            # Keep each journal's IDs and sequence local. Only the current
            # execution supplies the live stream, busy state and read cursor.
            value['previous_executions'] = []
            for row in conn.execute('SELECT id,round,generation,requirement_version FROM goal_executions '
                                    'WHERE goal_id=? AND session_id=? ORDER BY round,generation', (goal_id, session_id)):
                if row['id'] != value['execution_id']:
                    value['previous_executions'].append({'execution_id': row['id'], 'round': row['round'],
                        'generation': row['generation'], 'requirement_version': row['requirement_version'],
                        'events': self.manager.history(row['id'])['events']})
        return value

    def pause(self, goal_id, *, manual=True, actor=board.HUMAN):
        with self.manager.lock, closing(store.connect(self.home)) as conn:
            goals.request_pause(conn, goal_id, manual=manual, actor=actor)
            self.sync(conn, goal_id)
            return goals.get(conn, goal_id)

    def sync(self, conn, goal_id=None):
        """Runs from existing service work/state calls, never another polling thread."""
        unavailable = {row['session_id'] for row in conn.execute(
            'SELECT s.session_id FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id '
            "WHERE s.status='closed' OR c.archived_status IS NOT NULL OR c.removed=1")}
        for goal in conn.execute("SELECT id,leader_session_id FROM goals WHERE manual_pause=0 "
                                 "AND status NOT IN ('completed','failed','cancelled')" +
                                 (' AND id=?' if goal_id else ''), (goal_id,) if goal_id else ()).fetchall():
            if goal['leader_session_id'] in unavailable:
                goals.request_pause(conn, goal['id'], actor=goals._OPERATIONS, reason='主责 leader 已归档或移除')
        rows = conn.execute('SELECT e.id,e.session_id,e.goal_id,g.leader_session_id,g.status goal_status,e.status,e.round, '
                            'g.current_round FROM goal_executions e '
                            'JOIN goals g ON g.id=e.goal_id' + (' WHERE e.goal_id=?' if goal_id else ''),
                            (goal_id,) if goal_id else ()).fetchall()
        pausing = set()
        for row in rows:
            key = row['id']
            if row['session_id'] in unavailable or row['leader_session_id'] in unavailable:
                self.manager.stop_managed(key, reason='session_archive')
            current = self.manager.processes.get(key)
            stale = row['round'] != row['current_round']
            if row['status'] in goals.TERMINAL:
                if (getattr(current, 'runtime', None) == 'codex' and getattr(current, 'maintenance', None) is None
                        and self.manager.running(key) and not self.manager.busy(key)):
                    # Completed executions no longer own an interactive native
                    # sidebar entry; close() archives without deleting history.
                    current.close()
                continue
            if getattr(current, 'maintenance', None) is not None:
                # Maintenance compaction is neither goal work to interrupt nor a state to reconcile.
                continue
            if (not stale and goals.discussion(conn, key) and
                    goals.dispatch_check(conn, row['goal_id'], execution_id=key, allow_discussion=True)['allowed']):
                state = 'running' if self.manager.busy(key) else 'idle' if self.manager.running(key) else 'offline'
                if state != row['status']:
                    goals.set_execution_status(conn, key, state)
                continue
            if row['goal_status'] in ('pausing', 'paused') or row['goal_status'] in goals.TERMINAL or stale:
                if current is None and row['status'] in ('running', 'pausing'):
                    execution = goals.get_execution(conn, key)
                    if execution['runtime'] == 'claude' and (self.live_native is None or any(
                            b['runtime'] == 'claude' and b['native_id'] in self.live_native for b in execution['bindings'])):
                        continue
                    if execution['runtime'] == 'codex':
                        from .codex_runtime import RuntimeLease
                        try:
                            lease = RuntimeLease(self.home, key).acquire()
                        except board.Conflict:
                            continue
                        lease.release()
                if self.manager.busy(key):
                    if not getattr(current, 'pause_interrupt', False):
                        self.manager.interrupt(key)
                        current.pause_interrupt = True
                elif row['status'] not in goals.TERMINAL:
                    goals.set_execution_status(conn, key, 'cancelled' if stale else 'paused' if row['goal_status'] in ('pausing','paused') else row['goal_status'])
                if row['goal_status'] == 'pausing':
                    pausing.add(row['goal_id'])
            elif current is not None and row['status'] not in goals.TERMINAL:
                state = 'running' if self.manager.busy(key) else 'idle' if self.manager.running(key) else 'offline'
                if state != row['status']:
                    goals.set_execution_status(conn, key, state)
        for identity in pausing:
            executions = conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (identity,)).fetchall()
            if not any(self.manager.busy(row['id']) for row in executions):
                goals.confirm_paused(conn, identity)
        pending = conn.execute("SELECT goal_id FROM goal_round_finishes WHERE status='pending'" +
                               (' AND goal_id=?' if goal_id else ''), (goal_id,) if goal_id else ()).fetchall()
        for row in pending:
            goals.finalize_finish(conn, row['goal_id'])
        cancellations = conn.execute('SELECT goal_id FROM goal_cancellations' +
                                     (' WHERE goal_id=?' if goal_id else ''), (goal_id,) if goal_id else ()).fetchall()
        for row in cancellations:
            goals.finalize_cancel(conn, row['goal_id'])
        running = conn.execute("SELECT id,leader_session_id FROM goals WHERE status='running' AND manual_pause=0" +
                               (' AND id=?' if goal_id else ''), (goal_id,) if goal_id else ()).fetchall()
        for value in running:
            blocked = conn.execute("SELECT 1 FROM goal_links l LEFT JOIN decisions d ON l.kind='decision' AND d.id=l.item_id "
                "LEFT JOIN todos t ON l.kind='todo' AND t.id=l.item_id JOIN goals g ON g.id=l.goal_id "
                "WHERE l.goal_id=? AND l.round=g.current_round "
                "AND ((d.status='open' AND d.blocking=1) OR (t.status='open' AND t.blocking=1)) LIMIT 1", (value['id'],)).fetchone()
            executions = conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (value['id'],)).fetchall()
            if blocked and not any(self.manager.busy(e['id']) for e in executions):
                goals.transition(conn, value['id'], 'waiting_user', actor=value['leader_session_id'])

    def recover(self, conn, agents, runtime=None):
        """One restart snapshot; only previously running current executions resume."""
        if self.recovery is None:
            self.recovery = {row['id'] for row in conn.execute(
                "SELECT e.id FROM goal_executions e JOIN goals g ON e.goal_id=g.id "
                "WHERE g.status='running' AND g.manual_pause=0 AND e.status='running' "
                "AND e.round=g.current_round")
                if row['id'] not in self.manager.processes
                and row['id'] not in self.manager.runtime_recovery.startup_exclusions}
        live = {agent['sessionId'] for agent in agents}
        for key in list(self.recovery):
            if key in self.manager.runtime_recovery.startup_exclusions:
                self.recovery.remove(key)
                continue
            execution = goals.get_execution(conn, key)
            if native_handoff.blocked(conn, execution['session_id'], key):
                continue
            if runtime is not None and execution['runtime'] != runtime:
                continue
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=key)
            if not gate['allowed'] or self.manager.running(key):
                self.recovery.remove(key)
                continue
            if any(b['runtime'] == 'claude' and b['native_id'] in live for b in execution['bindings']):
                continue
            try:
                self.send(execution['goal_id'], execution['session_id'],
                          behavior_prompts.text(conn, 'resume_nudge', session_id=execution['session_id'], values={'reason': 'Crew 服务重启，本目标上次的工作被打断'}),
                          identity='recovery:' + self.epoch + ':' + key, source='crew')
            except (board.BoardError, OSError, subprocess.SubprocessError) as error:
                detail = type(error).__name__ + ': ' + str(error)
                if self.recovery_errors.get(key) != detail:
                    self.manager.journal(key).emit('error', text='目标续接未执行：' + detail)
                    self.recovery_errors[key] = detail
                if isinstance(error, board.Conflict):
                    continue
            self.recovery.remove(key)

    def native_ids(self, conn):
        return {row['native_id'] for row in conn.execute('SELECT native_id FROM goal_execution_bindings')}

    def resume_operations(self, conn):
        for row in conn.execute('SELECT * FROM goal_ops_pauses WHERE resumed_at IS NOT NULL'):
            token = json.loads(row['token_json'])
            goal = goals.get(conn, row['goal_id'])
            if (goal['status'] != 'running' or goal['manual_pause'] or goal['current_round'] != token['round']
                    or goal['requirement_version'] != token['requirement_version']
                    or goals._revision(conn, goal['id']) != token['control_revision']):
                continue
            keys = token.get('execution_ids')
            if keys is None:
                current = self.current(conn, goal['id'], goal['leader_session_id'])
                keys = [current['id']] if current else []
            for key in keys:
                if conn.execute('SELECT 1 FROM goal_resumption_deliveries WHERE goal_id=? AND incident_id=? AND execution_id=?',
                                (goal['id'], row['incident_id'], key)).fetchone() or self.manager.busy(key):
                    continue
                execution = goals.get_execution(conn, key)
                if not goals.dispatch_check(conn, goal['id'], execution_id=key)['allowed']:
                    continue
                identity = 'ops-resume:' + token['id'] + ':' + key
                prior = self.manager.journal(key).inputs.get(identity)
                if prior and prior['status'] in ('submitted', 'unknown'):
                    continue
                self.send(goal['id'], execution['session_id'], behavior_prompts.text(conn, 'resume_nudge', session_id=execution['session_id'], values={'reason': 'Crew 运维事故已恢复，续接暂停前的当前目标工作'}),
                          identity=identity, source='crew')
                conn.execute('INSERT OR IGNORE INTO goal_resumption_deliveries VALUES(?,?,?,?,?)',
                             (goal['id'], row['incident_id'], key, identity, store.now_iso()))
