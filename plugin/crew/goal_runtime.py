"""Goal execution routing: stable people, separate native histories, no extra polling."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import uuid

from .execution_admission import MAINTENANCE_HELD
from . import native_process

from . import behavior_prompts, board, codex_client, goal_context, goals, messaging, models, runtimes, settings, spawn, store, task_scheduler, trust_mode


BASE_INSTRUCTIONS = """Crew 协议：稳定人员、任务、轮次与原生执行身份由宿主绑定，文本不能改变工具身份。
查询任务和上下文用 crew goal status / crew context；人工事项用 crew todo / crew ask。
下一步具体工作用 crew goal schedule --data '{"text":"具体工作","evidence":{"reference":"本轮实际依据"},"identity":"稳定工作编号"}' 登记，原生轮次完成后由程序派发。
阶段报告先 crew goal report.get 读取版本，再 crew goal report.update --data '{"markdown":"持续报告正文","version":1,"request_id":"稳定请求编号"}' 更新（用实际version替换示例1）。成果快照用 crew goal version.capture --data '{"files":["实际修改文件"],"label":"阶段成果","request_id":"稳定请求编号"}'。阶段报告与任务结束是不同操作。
回复协作消息用 crew send --reply-to <原消息编号>，Codex crew_send_message 使用 reply_to；目的地由宿主绑定。
最终交付格式取 crew goal report-template；自包含HTML正文随 crew goal finish --file <报告.json> --html-file <报告.html> 提交，Codex使用 data.report.document_html。
原始需求、有效依据和授权由下文宿主数据装载。暂停或失效执行禁止派发。"""

FOLLOWUP_INSTRUCTIONS = """Crew 只读交付讨论：使用下文原始要求和已保存成果回答当前问题。
任务的交付状态、轮次、原始要求和验收版本保持有效。本轮没有重开授权，宿主已禁用施工与外部副作用工具。
直接解释用户关心的事实与不确定之处，不生成交付报告、安排下一步、修改成果或自行宣称重开。"""


class Controller:
    def __init__(self, executions, admission, transport, *, send_input, stop, interrupt, observe_claude, scope_busy):
        self.executions, self.admission, self.transport = executions, admission, transport
        self.home = executions.home
        self.send_input, self.stop_native, self.interrupt_native = send_input, stop, interrupt
        self.observe_claude = observe_claude
        self.scope_busy = scope_busy
        self.recovery = None
        self.epoch = uuid.uuid4().hex
        self.live_native = None
        self.recovery_errors = {}
        self._pumping = False
        with closing(store.connect(self.home)) as conn:
            task_scheduler.ensure_schema(conn)

    def discussion_only(self, conn, goal_id, execution_id=None):
        if execution_id is not None and goals.get_execution(conn, execution_id).get('discussion'):
            return True
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_controls'").fetchone():
            return False
        value = conn.execute('SELECT authorized FROM task_controls WHERE goal_id=?', (goal_id,)).fetchone()
        return value is not None and not value['authorized']

    def mode_context(self, conn, goal_id):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_controls'").fetchone():
            return trust_mode.context(conn, goals.get(conn, goal_id)['leader_session_id']) or ''
        value = conn.execute('SELECT mode,authorized FROM task_controls WHERE goal_id=?', (goal_id,)).fetchone()
        if value is None:
            return ''
        if not value['authorized']:
            return '本任务仍在讨论，尚未授权施工。只回答、澄清和提炼需求；宿主已限制原生工具，不能启动施工。'
        return ('本任务决策模式：全自动。只在本任务原始目标和约束内自主选择路线，真实资料、凭证及人工操作仍创建TODO。'
                if value['mode'] == 'automatic' else
                '本任务决策模式：用户把关。新框架、审美、交互体验与重要效果取舍在原任务拍板，其他明确工作继续。')

    def schedule(self, goal_id, session_id, text, *, identity=None, evidence=None):
        with self.locked(), closing(store.connect(self.home)) as conn:
            self.consume_events(conn, goal_id)
            execution = self.current(conn, goal_id, session_id)
            after_input = task_scheduler.active_input(conn, execution['id']) if execution and self.executions.busy(execution['id']) else None
            return task_scheduler.queue(conn, goal_id, session_id, text, identity=identity, evidence=evidence,
                after_execution=execution['id'] if after_input else None, after_input=after_input)

    def interrupt_turn(self, execution_id):
        with closing(store.connect(self.home)) as conn:
            task_scheduler.interrupt(conn, execution_id)

    def stop_discussions(self, conn, goal_id):
        """Stop only read-only followups, retaining the task's delivered state."""
        goals.get(conn, goal_id)
        pending, errors = [], []
        live_claude = None
        with self.locked():
            rows = conn.execute('SELECT e.id FROM goal_executions e JOIN task_discussions d ON d.execution_id=e.id '
                                'WHERE e.goal_id=?', (goal_id,)).fetchall()
            for row in rows:
                key = row['id']
                execution = goals.get_execution(conn, key)
                current = self.executions.current(key)
                conn.execute('UPDATE task_discussions SET stop_requested_at=?,stopped_at=NULL WHERE execution_id=?', (store.now_iso(), key))
                try:
                    if execution['status'] not in goals.TERMINAL:
                        goals.set_execution_status(conn, key, 'pausing')
                    task_scheduler.interrupt(conn, key)
                    if current is None:
                        if execution['runtime'] == 'claude':
                            if live_claude is None:
                                live_claude = {agent['sessionId'] for agent in self.observe_claude()}
                            if any(binding['runtime'] == 'claude' and binding['native_id'] in live_claude for binding in execution['bindings']):
                                raise board.Conflict('只读讨论仍在外部原生进程运行，未确认停止')
                        else:
                            from .codex_runtime import RuntimeLease
                            lease = RuntimeLease(self.home, key).acquire()
                            lease.release()
                    else:
                        if self.executions.busy(key):
                            self.interrupt_native(key)
                        self.stop_native(key)
                    if execution['status'] not in goals.TERMINAL:
                        goals.set_execution_status(conn, key, 'offline')
                    conn.execute('UPDATE task_discussions SET stopped_at=? WHERE execution_id=?', (store.now_iso(), key))
                except (board.BoardError, codex_client.CodexError, OSError, subprocess.SubprocessError) as error:
                    detail = type(error).__name__ + ': ' + str(error)
                    pending.append(key)
                    errors.append({'execution_id': key, 'error': detail})
                    if execution['status'] not in goals.TERMINAL:
                        goals.set_execution_status(conn, key, 'pausing', error=detail)
                    self.executions.journal(key).emit('error', text='只读讨论停止尚未确认：' + detail)
        return {'stopped': not pending, 'pending': pending, 'errors': errors, 'matched': len(rows)}

    def consume_events(self, conn, goal_id=None):
        """Standard journal events are durable and never call back from readers."""
        rows = conn.execute('SELECT DISTINCT execution_id FROM task_turns' + (' WHERE goal_id=?' if goal_id else ''),
                            (goal_id,) if goal_id else ()).fetchall()
        for row in rows:
            key = row['execution_id']
            if not self.executions.path(key).exists():
                continue
            journal = self.executions.journal(key)
            with journal.condition:
                events = list(journal.events)
            for result in task_scheduler.consume(conn, key, events):
                # This runs under the coordinator, never under a native reader
                # waiting for the registry lock held by process shutdown.
                if not any(item['type'] == 'task_turn' and item.get('input_id') == result['input_id'] for item in journal.events):
                    journal.emit('task_turn', **result)

    def pump(self, conn):
        if self._pumping or self.admission.paused() is not None:
            return
        self._pumping = True
        try:
            self._pump(conn)
        finally:
            self._pumping = False

    def _pump(self, conn):
        for row in conn.execute("SELECT id,execution_id,message_id FROM task_work WHERE status='dispatching'").fetchall():
            prior = self.executions.journal(row['execution_id']).inputs.get(row['message_id'])
            task_scheduler.receipt(conn, row['id'], accepted=bool(prior and prior['status'] == 'accepted'),
                unknown=True, error=None if prior and prior['status'] == 'accepted' else '派发回执未确认；先核对原生历史，禁止盲目重发')
        for work in task_scheduler.ready(conn):
            if not goals.dispatch_check(conn, work['goal_id'], round=work['round'], requirement_version=work['requirement_version'])['allowed']:
                continue
            execution = self.current(conn, work['goal_id'], work['session_id'])
            if execution and self.executions.busy(execution['id']):
                continue
            if self.executions.maintenance_held(work['session_id']) or messaging.stopped(conn, work['session_id']):
                continue
            try:
                self.check_member_start(conn, work['goal_id'], work['session_id'], 'crew')
                execution = self.ensure(conn, work['goal_id'], work['session_id'])
                claimed = task_scheduler.claim(conn, work['id'], execution['id'])
                text = work['text']
                if work['recoveries']:
                    text = ('上次此项工作失败：' + str(work['error']) + '\n先核对已有结果与实际副作用，'
                            '已经成功的步骤不得重做，只处理尚未完成的部分。\n本项工作：\n' + text)
                result = self.send(work['goal_id'], work['session_id'], text, identity=claimed['message_id'], source='crew', intent='work')
                task_scheduler.receipt(conn, work['id'], accepted=result.get('accepted') is True)
            except (board.BoardError, codex_client.CodexError, OSError, subprocess.SubprocessError) as error:
                record = conn.execute('SELECT execution_id,message_id,status FROM task_work WHERE id=?', (work['id'],)).fetchone()
                if record['status'] == 'dispatching':
                    prior = self.executions.journal(record['execution_id']).inputs.get(record['message_id'])
                    turn = conn.execute('SELECT status FROM task_turns WHERE execution_id=? AND input_id=?',
                                        (record['execution_id'], record['message_id'])).fetchone()
                    if turn and turn['status'] == 'rejected' and prior is None:
                        conn.execute("UPDATE task_work SET status='queued',error=?,updated_at=? WHERE id=?",
                                     (type(error).__name__ + ': ' + str(error), store.now_iso(), work['id']))
                        continue
                    ambiguous = prior and prior['status'] in ('unknown', 'submitted')
                    detail = type(error).__name__ + ': ' + str(error)
                    task_scheduler.receipt(conn, work['id'], accepted=False, unknown=bool(ambiguous), error=detail)
                    if not ambiguous:
                        task_scheduler.complete(conn, record['execution_id'], record['message_id'], success=False, error=detail)
                elif record['status'] == 'queued':
                    detail = type(error).__name__ + ': ' + str(error)
                    conn.execute('UPDATE task_work SET error=?,updated_at=? WHERE id=? AND error IS NOT ?',
                                 (detail, store.now_iso(), work['id'], detail))

    def locked(self):
        """Hold the shared execution-state lock, without starting a DB transaction."""
        return self.executions.locked()

    def agent_action(self, action, data, actor_session_id, execution_id=None):
        from . import goal_actions
        with self.locked():
            return goal_actions.dispatch(self.home, actor_session_id, execution_id, action, data, controller=self)

    def current(self, conn, goal_id, session_id):
        goal = goals.get(conn, goal_id)
        goals._member(conn, goal, session_id)
        rows = conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND round=? AND session_id=? '
                            'AND requirement_version=? ORDER BY generation DESC',
                            (goal_id, goal['current_round'], session_id, goal['requirement_version'])).fetchall()
        return None if not rows else goals.get_execution(conn, rows[0]['id'])

    def active_executions(self, conn, session_id):
        """Current task scopes for display; never infer a message destination."""
        rows = conn.execute("SELECT e.id FROM goal_executions e JOIN goals g ON g.id=e.goal_id "
            "WHERE e.session_id=? AND e.round=g.current_round AND e.requirement_version=g.requirement_version "
            "AND g.status IN ('queued','running','pausing','paused','waiting_user') "
            "AND e.status NOT IN ('completed','failed','cancelled') "
            "AND e.generation=(SELECT MAX(x.generation) FROM goal_executions x WHERE x.goal_id=e.goal_id "
            "AND x.round=e.round AND x.session_id=e.session_id) "
            "ORDER BY CASE WHEN g.status='running' THEN 0 ELSE 1 END,g.updated_at DESC,e.created_at DESC",
            (session_id,)).fetchall()
        values = [goals.get_execution(conn, row['id']) for row in rows]
        return sorted(values, key=lambda item: (not self.executions.busy(item['id']), not self.executions.running(item['id'])))

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

    def ensure(self, conn, goal_id, session_id, *, allow_discussion=False, followup=False):
        goal = goals.get(conn, goal_id)
        if followup and conn.execute('SELECT 1 FROM task_discussions d JOIN goal_executions e ON e.id=d.execution_id '
                'WHERE e.goal_id=? AND e.session_id=? AND d.stop_requested_at IS NOT NULL AND d.stopped_at IS NULL LIMIT 1',
                (goal_id, session_id)).fetchone():
            raise board.Conflict('只读讨论仍在停止中，请等待原生进程与输出结束')
        gate = goals.dispatch_check(conn, goal_id, automatic=not allow_discussion, followup=followup)
        if not gate['allowed']:
            raise board.Conflict(gate['reason'])
        person = board.session(conn, session_id)
        selection_audit = None
        if person.get('entrypoint') == 'crew-task' and conn.execute(
                'SELECT 1 FROM session_runtimes WHERE session_id=?', (session_id,)).fetchone() is None:
            selected = settings.resolve(conn, purpose=person['role'], project=person['project'], session_id=session_id)
            runtimes.set(conn, session_id, selected['runtime'])
            models.set(conn, session_id, selected['model'], selected['effort'], runtime=selected['runtime'])
            if selected.get('resolution') is not None:
                selection_audit = {'role': person['role'],
                    'selection': {key: selected[key] for key in ('runtime', 'model', 'effort')},
                    'resolution': selected['resolution']}

        def recorded(value):
            if selection_audit is not None:
                detail = {'goal_id': goal_id, 'execution_id': value['id'], **selection_audit}
                board._event(conn, 'runtime:selected', person['project'], session_id, detail, None)
                self.executions.journal(value['id']).emit('runtime_selection', **detail)
            return value

        execution = self.current(conn, goal_id, session_id)
        if followup and execution is not None and not execution.get('discussion'):
            if self.executions.busy(execution['id']):
                raise board.Conflict('原生交付轮次仍在结束中，请等待后再讨论')
            if self.executions.running(execution['id']):
                self.stop_native(execution['id'])
            execution = None
        if execution is not None and execution['status'] not in goals.TERMINAL:
            if execution.get('discussion') and conn.execute('SELECT 1 FROM task_discussions WHERE execution_id=? '
                    'AND stop_requested_at IS NOT NULL AND stopped_at IS NULL', (execution['id'],)).fetchone():
                raise board.Conflict('只读讨论仍在停止中，请等待原生进程与输出结束')
            current = self.executions.current(execution['id'])
            if current is not None and self.executions.running(execution['id']) and bool(getattr(current, 'discussion_only', False)) != self.discussion_only(conn, goal_id, execution['id']):
                if self.executions.busy(execution['id']):
                    raise board.Conflict('任务授权已变化；当前讨论轮结束后，下轮重新装载原生执行权限')
                self.stop_native(execution['id'])
            mapping, options = runtimes.get(conn, session_id), models.get(conn, session_id)
            if execution['runtime'] != mapping['runtime']:
                if self.executions.busy(execution['id']):
                    raise board.Conflict('更换载体前请先暂停当前目标')
                if self.executions.running(execution['id']):
                    self.stop_native(execution['id'])
                if execution.get('discussion'):
                    goals.set_execution_status(conn, execution['id'], 'cancelled')
                    return recorded(goals.create_execution(conn, goal_id, session_id, runtime=mapping['runtime'],
                        model=options['model'], effort=options['effort'], allow_discussion=True, followup=True))
                goals.configure_execution(conn, execution['id'], runtime=mapping['runtime'], **options)
                execution = goals.get_execution(conn, execution['id'])
            if execution['status'] in ('offline', 'paused'):
                goals.set_execution_status(conn, execution['id'], 'queued', allow_discussion=allow_discussion)
                execution = goals.get_execution(conn, execution['id'])
            return recorded(execution)
        if not any(item['session_id'] == session_id and item['status'] == 'assigned' for item in goal['assignments']):
            goals.assign(conn, goal_id, session_id, '处理用户在此目标内的输入')
        mapping, options = runtimes.get(conn, session_id), models.get(conn, session_id)
        return recorded(goals.create_execution(conn, goal_id, session_id, runtime=mapping['runtime'],
                                      model=options['model'], effort=options['effort'], allow_discussion=allow_discussion, followup=followup))

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
            for event in self.executions.journal(row['id']).events:
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

    def followup_context(self, conn, execution):
        """A new round continues the last delivery and the human's review."""
        row = conn.execute('SELECT id FROM goal_reports WHERE goal_id=? AND round' + ('<=?' if execution.get('discussion') else '<?') + ' ORDER BY round DESC,version DESC LIMIT 1',
                           (execution['goal_id'], execution['round'])).fetchone()
        if row is None:
            return []
        report = goals.get_report(conn, row['id'])
        data = report['data']
        review = conn.execute('SELECT accepted,note FROM goal_acceptances WHERE goal_id=? AND round=?',
                              (execution['goal_id'], report['round'])).fetchone()
        text = '上一轮交付（历史证据，不代表本轮已完成）：报告 %s，第 %s 轮。\n%s' % (
            report['id'], report['round'], json.dumps({key: data[key] for key in
                ('summary', 'delivered', 'unresolved', 'artifacts', 'follow_up')}, ensure_ascii=False))
        if review is not None:
            text += '\n用户对该轮的验收：%s。原话：%s' % ('通过' if review['accepted'] else '需要修改', review['note'])
        text += ('\n本轮是只读交付讨论，解释这些已保存成果，不重开任务、不改变要求、不施工。' if execution.get('discussion') else
                 '\n沿用已有结果处理用户的新输入，不重复施工；完整报告可用 crew goal status 查阅。')
        return [{'source': {'type': 'goal-report', 'id': report['id']}, 'text': text,
                 'reason': '继续本任务时保留上轮交付与用户修改意见', 'protected': True}]

    def stop_delivered(self, conn, goal_id):
        """Drain delivered runtimes before a human review or follow-up round."""
        idle, agents = [], None
        for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (goal_id,)):
            key = row['id']
            current = self.executions.current(key)
            if self.executions.running(key):
                if self.executions.busy(key):
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
                            agents = self.observe_claude()
                        if any(agent['sessionId'] in identities for agent in agents):
                            raise board.Conflict('原生执行仍在外部运行，尚不能验收或继续修改')
                else:
                    from .codex_runtime import RuntimeLease
                    lease = RuntimeLease(self.home, key).acquire()
                    lease.release()
        for key in idle:
            self.stop_native(key)
        self.sync(conn, goal_id)

    def launch(self, session, spec):
        """Called under the shared execution-state lock; never mutates the person's default native binding."""
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
        if prior is None and self.scope_busy(session_id, **runtimes.get(conn, session_id)):
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
        execution = session['_execution']
        key, logical_id = execution['id'], execution['session_id']
        previous = self.executions.current(key)
        if previous is not None and not previous.reader_done.wait(10):
            raise board.BoardError('上一轮目标执行的输出尚未排空，请稍后重试；未启动新进程')
        with closing(store.connect(self.home)) as conn:
            discussion_only = self.discussion_only(conn, execution['goal_id'], key)
            if maintenance is None:
                gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=key, automatic=not discussion_only)
                if not gate['allowed']:
                    raise board.Conflict(gate['reason'])
            permission_mode = settings.effective(conn, project=session['project'], session_id=logical_id,
                purpose=session['role'] if session['role'] in ('leader', 'member', 'assistant') else 'member')['execution']['claude']
        options = {'model': execution['model'], 'effort': execution['effort']}
        binding = next((item for item in execution['bindings'] if item['runtime'] == execution['runtime']), None)
        if execution['runtime'] == 'claude' and binding is not None:
            if self.live_native is None:
                raise board.Conflict('Claude 进程状态尚未确认，不能重复恢复目标执行')
            if binding['native_id'] in self.live_native:
                raise board.Conflict('目标原生会话仍在外部运行，等待它退出后续接')
        if execution['runtime'] == 'codex':
            self.transport.codex(session, spec, options, resume=binding is not None, execution=execution,
                                  goal_handler=self.agent_action, maintenance=maintenance, discussion_only=discussion_only)
            return spawn.command(key, session['crew_name'], session['role'], runtime='codex', **options)
        native_id = binding['native_id'] if binding else str(uuid.uuid4())
        if binding is None:
            with closing(store.connect(self.home)) as conn:
                goals.bind_native(conn, key, 'claude', native_id, **options)
        command = spawn.command(native_id, session['crew_name'], session['role'], binding is not None,
                                instructions=('你是 Crew「%s」的当前目标执行。目标规则和用户原始要求由宿主随输入装载；仅当前明确授权有效。' % session['crew_name']
                                              + ('\n' + spec['_workspace_notice'] if spec.get('_workspace_notice') else '')),
                                permission_mode=permission_mode, plugin_root=self.transport.plugin_root,
                                **options)
        command[0] = native_process.claude_executable()
        if discussion_only:
            command.extend(['--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands'])
        environment = native_process.child_environment({**spec, 'name': session['crew_name'], 'role': session['role'],
                                               'group': session['group_name']})
        environment.update(CREW_HOME=str(self.home.resolve()), CREW_SESSION_ID=logical_id,
                           CREW_EXECUTION_ID=key, CREW_GOAL_ID=execution['goal_id'])
        environment['PATH'] = str(self.home.resolve() / 'bin') + os.pathsep + environment.get('PATH', '')
        self.transport.claude(command, key=key, cwd=session['cwd'], environment=environment,
                               execution=execution, native_id=native_id, maintenance=maintenance,
                               goal_manifests=session.get('_goal_manifests', {}), name='crew-goal-claude', discussion_only=discussion_only)
        return command

    def send(self, goal_id, session_id, text, *, identity=None, images=None, source='human', display_text=None, sender=None, intent='dialogue'):
        with self.admission.work():
            return self._send(goal_id, session_id, text, identity=identity, images=images, source=source, display_text=display_text, sender=sender, intent=intent)

    def _send(self, goal_id, session_id, text, *, identity=None, images=None, source='human', display_text=None, sender=None, intent='dialogue'):
        identity = str(uuid.uuid4()) if identity is None else identity
        goals._text(identity, '消息标识', limit=160)
        goals._text(text, '消息文字', required=False)
        if intent not in ('work', 'dialogue'):
            raise board.BoardError('执行意图必须是 work 或 dialogue')
        if not text.strip() and not images:
            raise board.BoardError('请提供文字或图片')
        with self.locked(), closing(store.connect(self.home)) as conn:
            self.consume_events(conn, goal_id)
            goal = goals.get(conn, goal_id)
            goals._member(conn, goal, session_id)
            if self.executions.maintenance_held(session_id):
                # Refuse before any goal or execution state changes.
                raise board.Conflict(MAINTENANCE_HELD)
            human = source in ('human', 'qq', 'lark')
            followup = human and intent == 'dialogue' and goals.task_profile(conn, goal_id) and goal['status'] in ('completed', 'failed', 'awaiting_acceptance')
            discussion_only = followup or self.discussion_only(conn, goal_id)
            if discussion_only:
                intent = 'dialogue'
            if human:
                # Retry the original receipt even if a later report closed its
                # round. A transport retry is never a new continuation request.
                for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=? ORDER BY round DESC,generation DESC',
                                        (goal_id, session_id)):
                    prior_execution = goals.get_execution(conn, row['id'])
                    previous = self.executions.journal(row['id']).inputs.get(identity)
                    if previous is not None and previous['status'] != 'rejected':
                        result = self.send_input(self.target(conn, prior_execution), text, identity=identity,
                            images=images, source=source, display_text=display_text, sender=sender)
                        return {**result, 'goal_id': goal_id, 'execution_id': row['id']}
                # Only a new human input can change intent. Replaying a known
                # transport receipt above must preserve later stops/failures.
                if not followup and task_scheduler.blocked(conn, goal_id):
                    task_scheduler.clear_block(conn, goal_id)
                    if goal['status'] == 'paused' and not goal['manual_pause']:
                        goal = goals.resume(conn, goal_id, actor=board.HUMAN)
                if not followup and goal['status'] in ('completed', 'failed', 'awaiting_acceptance'):
                    self.stop_delivered(conn, goal_id)
                    goal = goals.reopen(conn, goal_id, text, actor=board.HUMAN)
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
            execution = self.ensure(conn, goal_id, session_id, allow_discussion=human and discussion_only, followup=followup)
            if goal['status'] == 'queued':
                goals.transition(conn, goal_id, 'running')
            key = execution['id']
            journal = self.executions.journal(key)
            if not journal.path.exists():
                journal.set_status('idle')
            target = self.target(conn, execution)
            prior = journal.inputs.get(identity)
            if prior is not None and prior['status'] != 'rejected':
                result = self.send_input(target, text, identity=identity, images=images, source=source,
                                           display_text=display_text, sender=sender)
                return {**result, 'goal_id': goal_id, 'execution_id': key}
            observed = self.executions.context(key)
            person = board.session(conn, session_id)
            role = person['role'] if person['role'] in ('leader', 'member', 'assistant') else 'member'
            prompt_manifest = behavior_prompts.resolve(conn, project=person['project'], session_id=session_id,
                keys=('common', role, 'context') if followup else ('common', role, 'report', 'context'))
            base_text = (FOLLOWUP_INSTRUCTIONS if followup else BASE_INSTRUCTIONS) + '\n' + behavior_prompts.render(prompt_manifest)
            if not followup:
                base_text += '\n' + self.mode_context(conn, goal_id)
            history = {'known': observed is not None, 'tokens': observed['used'] if observed else None,
                       'source': 'native_context_usage', 'note': '原生历史未知时不当作零'}
            prepared = goal_context.build(conn, key, base=base_text,
                                          latest_input='' if text == goal['requirements']['request'] else text,
                                          native_history=history, retrieval=self.human_handoff(conn, execution, identity) + self.followup_context(conn, execution),
                                          limits={'native_history_tokens': 64000})
            if prepared['manifest']['rotation_required'] and not self.executions.busy(key):
                if self.executions.running(key):
                    self.stop_native(key)
                goals.set_execution_status(conn, key, 'completed')
                execution = self.ensure(conn, goal_id, session_id, allow_discussion=human and discussion_only, followup=followup)
                key, target = execution['id'], self.target(conn, execution)
                journal = self.executions.journal(key)
                if not journal.path.exists():
                    journal.set_status('idle')
                prepared = goal_context.build(conn, key, base=base_text,
                                              latest_input='' if text == goal['requirements']['request'] else text,
                                              native_history={'known': True, 'tokens': 0,
                                              'source': 'fresh_native_generation'}, limits={'native_history_tokens': 64000},
                                              retrieval=self.human_handoff(conn, execution, identity) + self.followup_context(conn, execution))
            current = self.executions.current(key)
            if current is not None:
                current.pause_interrupt = False
                current.goal_manifests[identity] = prepared['manifest']['id']
            target['_goal_manifests'] = {identity: prepared['manifest']['id']}
            goals.set_execution_status(conn, key, 'running', allow_discussion=human and discussion_only)
            steering = self.executions.busy(key)
            if not steering:
                task_scheduler.observe_input(conn, key, identity, text, start_seq=len(journal.events), intent=intent)
            try:
                result = self.send_input(target, prepared['text'], display_text=text if display_text is None else display_text,
                                         identity=identity, images=images, source=source, sender=sender)
            except (board.BoardError, codex_client.CodexError, OSError, subprocess.SubprocessError) as error:
                receipt = journal.inputs.get(identity)
                if conn.execute('SELECT 1 FROM task_turns WHERE execution_id=? AND input_id=?', (key, identity)).fetchone():
                    if receipt is None and isinstance(error, board.BoardError):
                        task_scheduler.reject_input(conn, key, identity, type(error).__name__ + ': ' + str(error))
                    else:
                        task_scheduler.complete(conn, key, identity, success=False,
                            error=type(error).__name__ + ': ' + str(error),
                            unknown=bool(receipt and receipt['status'] in ('submitted', 'unknown')))
                raise
            if result.get('accepted') is True:
                if execution.get('discussion'):
                    conn.execute('UPDATE task_discussions SET stop_requested_at=NULL,stopped_at=NULL WHERE execution_id=?', (key,))
                if steering:
                    task_scheduler.observe_steer(conn, key, identity, intent=intent)
                if human and intent == 'work':
                    task_scheduler.reset_protocol(conn, goal_id)
            if result.get('accepted') is True and any(item['key'] == 'base' and not item.get('inherited') for item in prepared['manifest']['entries']):
                behavior_prompts.record_applied(conn, key, prompt_manifest, status='sent', identity='prompt:' + key + ':' + identity)
            if execution['runtime'] == 'codex' and not result.get('duplicate'):
                goal_context.mark_applied(conn, prepared['manifest']['id'], execution_id=key)
            return {**result, 'goal_id': goal_id, 'execution_id': key}

    def history(self, conn, goal_id, session_id, *, include_previous=True):
        execution = self.current(conn, goal_id, session_id)
        value = ({'events': [], 'busy': False, 'running': False} if execution is None
                 else self.executions.history(execution['id']))
        value.update(session_id=session_id, goal_id=goal_id,
                     execution_id=execution['id'] if execution else None,
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
                        'events': self.executions.history(row['id'])['events']})
        return value

    def pause(self, goal_id, *, manual=True, actor=board.HUMAN):
        with self.locked(), closing(store.connect(self.home)) as conn:
            goals.request_pause(conn, goal_id, manual=manual, actor=actor)
            if manual:
                task_scheduler.cancel_pending(conn, goal_id)
            self.sync(conn, goal_id)
            return goals.get(conn, goal_id)

    def sync(self, conn, goal_id=None):
        """Runs from existing service work/state calls, never another polling thread."""
        self.consume_events(conn, goal_id)
        rows = conn.execute('SELECT e.id,e.goal_id,g.status goal_status,e.status,e.round,e.requirement_version, '
                            'g.current_round,g.requirement_version current_version,d.execution_id discussion,d.stop_requested_at,d.stopped_at FROM goal_executions e '
                            'JOIN goals g ON g.id=e.goal_id LEFT JOIN task_discussions d ON d.execution_id=e.id' + (' WHERE e.goal_id=?' if goal_id else ''),
                            (goal_id,) if goal_id else ()).fetchall()
        pausing = set()
        for row in rows:
            key = row['id']
            current = self.executions.current(key)
            stale = row['round'] != row['current_round'] or row['requirement_version'] != row['current_version']
            if row['discussion'] and row['stop_requested_at'] and not row['stopped_at']:
                if current is not None and not self.executions.running(key) and current.reader_done.is_set():
                    conn.execute('UPDATE task_discussions SET stopped_at=? WHERE execution_id=?', (store.now_iso(), key))
                    if row['status'] not in goals.TERMINAL:
                        goals.set_execution_status(conn, key, 'offline')
                else:
                    continue
            if row['status'] in goals.TERMINAL:
                if (getattr(current, 'runtime', None) == 'codex' and getattr(current, 'maintenance', None) is None
                        and self.executions.running(key) and not self.executions.busy(key)):
                    # Completed executions no longer own an interactive native
                    # sidebar entry; close() archives without deleting history.
                    current.close()
                continue
            if getattr(current, 'maintenance', None) is not None:
                # Maintenance compaction is neither goal work to interrupt nor a state to reconcile.
                continue
            if row['discussion'] and not stale and row['goal_status'] in ('completed', 'failed', 'awaiting_acceptance'):
                if row['status'] == 'pausing':
                    if current is not None and not self.executions.running(key) and current.reader_done.is_set():
                        goals.set_execution_status(conn, key, 'offline')
                    continue
                if current is not None:
                    state = 'running' if self.executions.busy(key) else 'idle' if self.executions.running(key) else 'offline'
                    if state != row['status']:
                        goals.set_execution_status(conn, key, state, allow_discussion=True)
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
                if self.executions.busy(key):
                    if not getattr(current, 'pause_interrupt', False):
                        self.interrupt_native(key)
                        current.pause_interrupt = True
                elif row['status'] not in goals.TERMINAL:
                    goals.set_execution_status(conn, key, 'cancelled' if stale else 'paused' if row['goal_status'] in ('pausing','paused') else row['goal_status'])
                if row['goal_status'] == 'pausing':
                    pausing.add(row['goal_id'])
            elif current is not None and row['status'] not in goals.TERMINAL:
                state = 'running' if self.executions.busy(key) else 'idle' if self.executions.running(key) else 'offline'
                if state != row['status']:
                    goals.set_execution_status(conn, key, state, allow_discussion=self.discussion_only(conn, row['goal_id'], key))
        for identity in pausing:
            executions = conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (identity,)).fetchall()
            if not any(self.executions.busy(row['id']) for row in executions):
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
                "LEFT JOIN task_controls control ON control.goal_id=g.id "
                "WHERE l.goal_id=? AND l.round=g.current_round AND l.requirement_version=g.requirement_version "
                "AND ((d.status='open' AND d.blocking=1 AND COALESCE(control.mode,'manual')='manual') "
                "OR (t.status='open' AND t.blocking=1)) LIMIT 1", (value['id'],)).fetchone()
            executions = conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (value['id'],)).fetchall()
            if blocked and not any(self.executions.busy(e['id']) for e in executions):
                goals.transition(conn, value['id'], 'waiting_user', actor=value['leader_session_id'])
        task_scheduler.reconcile_protocol(conn, goal_id)
        self.pump(conn)

    def recover(self, conn, agents, runtime=None):
        """One restart snapshot; only previously running current executions resume."""
        self.consume_events(conn)
        if self.recovery is None:
            self.recovery = {row['id'] for row in conn.execute(
                "SELECT e.id FROM goal_executions e JOIN goals g ON e.goal_id=g.id "
                "WHERE g.status='running' AND g.manual_pause=0 AND (e.status='running' OR "
                "(e.status IN ('idle','offline') AND EXISTS(SELECT 1 FROM task_turns t WHERE t.execution_id=e.id "
                "AND t.status IN ('running','recovering') AND t.stopped_at IS NULL))) "
                "AND e.round=g.current_round AND e.requirement_version=g.requirement_version")}
        live = {agent['sessionId'] for agent in agents}
        for key in list(self.recovery):
            execution = goals.get_execution(conn, key)
            if runtime is not None and execution['runtime'] != runtime:
                continue
            latest = conn.execute('SELECT status,stopped_at,intent,work_id FROM task_turns WHERE execution_id=? ORDER BY rowid DESC LIMIT 1', (key,)).fetchone()
            if latest and latest['status'] == 'completed':
                # A completed input must never receive a generic restart prompt.
                # Reconcile a stale persisted running flag only after proving
                # its original native process is no longer owned elsewhere.
                if not self.executions.running(key):
                    if execution['runtime'] == 'claude' and not any(
                            b['runtime'] == 'claude' and b['native_id'] in live for b in execution['bindings']):
                        goals.set_execution_status(conn, key, 'offline')
                    elif execution['runtime'] == 'codex':
                        from .codex_runtime import RuntimeLease
                        try:
                            lease = RuntimeLease(self.home, key).acquire()
                        except board.Conflict:
                            self.recovery.remove(key)
                            continue
                        lease.release()
                        goals.set_execution_status(conn, key, 'offline')
                self.recovery.remove(key)
                continue
            if (latest and (latest['stopped_at'] or latest['status'] in ('interrupted', 'unknown', 'rejected'))) or conn.execute(
                    "SELECT 1 FROM task_work WHERE execution_id=? AND status IN ('unknown','dispatching')", (key,)).fetchone():
                self.recovery.remove(key)
                continue
            if latest and latest['status'] == 'failed' and conn.execute(
                    "SELECT 1 FROM task_work WHERE goal_id=? AND session_id=? AND status='queued' AND recoveries>0",
                    (execution['goal_id'], execution['session_id'])).fetchone():
                # A real failed native turn already produced one bounded recovery
                # event. Do not also send a generic restart prompt for the same work.
                self.recovery.remove(key)
                continue
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=key)
            if not gate['allowed'] or self.executions.running(key):
                self.recovery.remove(key)
                continue
            if any(b['runtime'] == 'claude' and b['native_id'] in live for b in execution['bindings']):
                continue
            try:
                identity = 'recovery:' + self.epoch + ':' + key
                task_scheduler.prepare_recovery(conn, key, identity)
                self.send(execution['goal_id'], execution['session_id'],
                          'Crew 服务重启；仅续接本目标上次未完成的工作。先核对实际代码与证据，已完成则收尾，不能重做或扩展任务。',
                          identity=identity, source='crew', intent=latest['intent'] if latest and not latest['work_id'] else 'work')
            except (board.BoardError, OSError, subprocess.SubprocessError) as error:
                detail = type(error).__name__ + ': ' + str(error)
                if self.recovery_errors.get(key) != detail:
                    self.executions.journal(key).emit('error', text='目标续接未执行：' + detail)
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
                                (goal['id'], row['incident_id'], key)).fetchone() or self.executions.busy(key):
                    continue
                execution = goals.get_execution(conn, key)
                if not goals.dispatch_check(conn, goal['id'], execution_id=key)['allowed']:
                    continue
                identity = 'ops-resume:' + token['id'] + ':' + key
                prior = self.executions.journal(key).inputs.get(identity)
                if prior and prior['status'] in ('submitted', 'unknown'):
                    continue
                latest = conn.execute('SELECT intent,work_id FROM task_turns WHERE execution_id=? ORDER BY rowid DESC LIMIT 1', (key,)).fetchone()
                self.send(goal['id'], execution['session_id'], 'Crew 运维事故已恢复；仅续接暂停前的当前目标工作，先核对已有结果，不重复施工。',
                          identity=identity, source='crew', intent=latest['intent'] if latest and not latest['work_id'] else 'work')
                conn.execute('INSERT OR IGNORE INTO goal_resumption_deliveries VALUES(?,?,?,?,?)',
                             (goal['id'], row['incident_id'], key, identity, store.now_iso()))
