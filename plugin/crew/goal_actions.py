"""Shared CLI/tool/HTTP business routes; actor and execution come from the host."""
from contextlib import closing
import hashlib
import json

from . import behavior_prompts, board, codex_client, goal_artifacts, goal_context, goal_mode, goal_reports, goal_work, goals, store

READ_ACTIONS = frozenset(('status', 'report-template', 'report.get', 'report.preview', 'context.inspect', 'context.retrieve', 'work.list')) | goal_artifacts.READ_ACTIONS
GOAL_ACTIONS = frozenset(('status', 'create', 'start', 'pause', 'resume', 'cancel', 'reopen',
                          'amend', 'assign', 'transfer', 'report-template', 'report.get', 'report.preview', 'report.publish', 'mode.set', 'finish', 'work.list', 'work.add', 'work.update')) | goal_artifacts.ACTIONS
CONTEXT_ACTIONS = frozenset(('context.inspect', 'context.retrieve', 'context.put', 'context.correct', 'context.expire'))
CREATE_FIELDS = frozenset(('title', 'request', 'acceptance', 'constraints', 'priority', 'dependencies', 'decision_mode',
                           'source', 'leader', 'leader_session_id'))
# Envelope keys the CLI and hosts add to every goal action.
TRANSPORT_FIELDS = frozenset(('project', 'goal_id', 'confirmed', 'start', 'request_id'))
CREATE_HELP = ('create 的 data 字段：title（标题）、request（用户原话）、acceptance（验收标准数组）、'
               '可选 constraints（数组）、priority、dependencies、decision_mode；'
               'start=true 建完立即由主责 leader 开工，不带则只排队等开始')


def report_template(conn, goal_id):
    goal = goals.get(conn, goal_id)
    return {
        'goal_id': goal_id, 'round': goal['current_round'], 'requirement_version': goal['requirement_version'],
        'outcome': 'awaiting_acceptance',
        'summary': '',
        'document_html': '',
        'acceptance': [
            {'criterion_id': item['id'], 'criterion': item['title'], 'status': 'unverified', 'evidence': []}
            for item in goal['requirements']['acceptance']
        ],
        '_help': {
            'outcome': 'awaiting_acceptance 交给用户验收；failed 或 cancelled 用于如实结束。',
            'acceptance': '覆盖全部标准。passed / failed 写 evidence [{label, reference}]，说明如何验证及在哪复查。',
            'unresolved': '有 unverified / simulated 时，用字符串数组说明验证边界。',
            'document_html': '自包含 HTML，最多 512 KiB。Crew CLI 可用 finish --html-file <报告.html>。',
            'folder': '有交付文件时填写所在目录（相对工作区或绝对路径）。',
            'user_test': '需要用户实测时填写 [{step, expected}]。',
            'human_input': '可选 user_todos [{title, detail}]、user_decisions [{question, options, recommendation, why}]。',
            'examples': '可选 [{kind: actual|illustrative, input, expected, actual, conclusion}]。',
        },
    }


def _person(conn, project, value):
    row = conn.execute('SELECT * FROM sessions WHERE session_id=?', (value,)).fetchone()
    person = dict(row) if row else board.named_session(conn, project, value)
    if person['project'] != project:
        raise board.BoardError('人员不属于当前项目')
    return person


def _host(controller):
    if controller is None:
        raise board.BoardError('此操作需要正在运行的 Crew 目标宿主；未启动或发送任何模型输入')
    return controller


BUSY_AUTOMATIC_INPUT = '自动消息等待当前轮次结束'
DELIVERED = frozenset(('awaiting_acceptance', 'completed', 'failed'))


def _user_turn(controller, session_id):
    """True when this conversation's current turn was started by the user's own message.

    Only Crew-hosted conversations record input provenance; an external session cannot prove it.
    """
    manager = getattr(controller, 'manager', None)
    if manager is None or not manager.path(session_id).exists():
        return False
    journal = manager.journal(session_id)
    with journal.condition:
        source = next((event.get('source') for event in reversed(journal.events) if event.get('type') == 'message'), None)
    return source == 'human'


def _deliver_amendment(conn, value, text, actor, controller, own_execution_id=None):
    """The amendment is committed; native delivery failure cannot undo it.

    The user's words use the human input path. A leader's amendment reaches
    every other running execution as a peer message; a busy native turn does
    not accept automatic input, so that delivery is deferred: the next input
    loads the current requirement through goal_context.build."""
    deliveries = []
    finish = goals.get_finish(conn, value['id'])
    if finish and finish['status'] == 'pending':
        return {**value, 'deliveries': deliveries}
    human = actor == board.HUMAN
    sender = None if human else board.session(conn, actor)['crew_name']
    message = ('【需求补充 v%s，%s】%s\n请判断对当前工作的影响：不受影响就继续；'
               '需要调整就在本轮内调整，不要从头重来，已成功的结果直接复用。') % (
        value['requirement_version'], '来自用户' if human else '来自负责人「%s」' % sender, text)
    options = {'source': 'human'} if human else {'source': 'peer', 'sender': sender}
    executions = conn.execute("SELECT id,session_id FROM goal_executions WHERE goal_id=? AND round=? AND status='running'",
                              (value['id'], value['current_round'])).fetchall()
    for execution in executions:
        if execution['id'] == own_execution_id:
            continue
        identity = 'amend:%s:v%s:%s' % (value['id'], value['requirement_version'], execution['id'])
        try:
            receipt = _host(controller).send(value['id'], execution['session_id'], message,
                                              identity=identity, **options)
            if receipt.get('accepted') is not True:
                raise board.Conflict('NativeReceiptUnknown: 原生接收未确认，请先核对会话')
        except Exception as error:
            if not human and isinstance(error, board.Conflict) and BUSY_AUTOMATIC_INPUT in str(error):
                deliveries.append({'execution_id': execution['id'], 'status': 'deferred',
                                   'error': '执行正忙，下一次输入时随最新需求装载'})
                continue
            # This post-commit delivery boundary explicitly reports every native failure.
            detail = type(error).__name__ + ': ' + str(error)
            deliveries.append({'execution_id': execution['id'], 'status': 'error', 'error': detail})
            with goals.transaction(conn):
                board._event(conn, 'goal:amend-delivery-error', value['project'], actor,
                             {'goal_id': value['id'], 'requirement_version': value['requirement_version'],
                              'execution_id': execution['id'], 'identity': identity, 'error': detail}, None)
        else:
            deliveries.append({'execution_id': execution['id'], 'status': 'sent', 'error': None})
    return {**value, 'deliveries': deliveries}


def _status_goal(value):
    # The immutable source and every round remain inspectable; a status read
    # must not repeat the same original request inside four nested objects.
    return {**{key: value[key] for key in ('id', 'title', 'leader_session_id', 'status',
        'current_round', 'requirement_version', 'version', 'manual_pause', 'reason', 'decision_mode')},
        'request': value['requirements']['request'], 'acceptance': value['requirements']['acceptance'],
        'constraints': value['requirements']['constraints']}


def _start_identity(goal, session_id, task=None):
    # A successful start is a finite action, including when a model retries it
    # with a new tool call ID. Later requirements/rounds get a new input identity.
    scope = '%s:%s:%s:%s' % (goal['id'], goal['current_round'], goal['requirement_version'], session_id)
    return 'goal-start:' + scope if task is None else 'goal-assign:' + scope + ':' + hashlib.sha256(task.encode('utf-8')).hexdigest()


def _started_work(conn, goal, person, actor_session_id, execution_id, receipt):
    if (execution_id is not None or actor_session_id != goal['leader_session_id']
            or actor_session_id != person['session_id'] or not receipt.get('accepted') or receipt.get('duplicate')):
        return receipt
    conn.execute('INSERT INTO general_goal_handoffs(session_id,goal_id) VALUES(?,?) '
                 'ON CONFLICT(session_id) DO UPDATE SET goal_id=excluded.goal_id', (actor_session_id, goal['id']))
    return {**receipt, 'handoff': '任务执行已接收输入，本轮总对话可以继续协调或启动其他任务。'
            '刚交接的工作由任务执行负责，简短告诉用户去对应任务看进展。'}


def _start(conn, controller, goal, person, actor_session_id, text=None, *, execution_id=None):
    goal_id = goal['id']
    goals._member(conn, goal, person['session_id'])
    controller.check_member_start(conn, goal_id, person['session_id'],
                                  'human' if actor_session_id == board.HUMAN else 'crew')
    # Check before leaving the queue, so a refused start does not show as running.
    gate = goals.dispatch_check(conn, goal_id)
    if not gate['allowed']:
        raise board.Conflict(gate['reason'])
    if goal['status'] == 'queued':
        goal = goals.transition(conn, goal_id, 'running', actor=actor_session_id)
    receipt = controller.send(goal_id, person['session_id'], text or goal['requirements']['request'],
                              identity=_start_identity(goal, person['session_id']),
                              source='human' if actor_session_id == board.HUMAN else 'crew')
    return _started_work(conn, goal, person, actor_session_id, execution_id, receipt)


def dispatch(home, actor_session_id, execution_id, action, data, controller=None):
    """Data is open JSON; it can never override the trusted actor/execution."""
    if action not in GOAL_ACTIONS | CONTEXT_ACTIONS or not isinstance(data, dict):
        raise board.BoardError('目标 action 或 data 无效')
    data = json.loads(goals._json(data))
    actor_session_id = board.HUMAN if actor_session_id is None else actor_session_id
    with closing(store.connect(home)) as conn:
        actor = None if actor_session_id == board.HUMAN else board.session(conn, actor_session_id)
        if actor and (actor['status'] == 'closed' or board.is_archived(conn, actor_session_id) or not actor['crew_name']):
            raise board.BoardError('已归档或未收编身份不能调用目标操作')
        execution = goals.get_execution(conn, execution_id) if execution_id else None
        if execution and (actor_session_id != execution['session_id']):
            raise board.BoardError('execution 与稳定 Crew 身份不匹配')
        goal_id = data.get('goal_id') or (execution['goal_id'] if execution else None)
        goal = goals.get(conn, goal_id) if goal_id else None
        project = actor['project'] if actor else goal['project'] if goal else data.get('project')
        if not project:
            raise board.BoardError('请明确项目或目标')
        board.project(conn, project)
        if data.get('project') is not None and data['project'] != project:
            raise board.BoardError('请求项目与绑定身份不匹配')
        if goal:
            if goal['project'] != project:
                raise board.BoardError('目标不属于当前项目')
            if actor:
                goals._member(conn, goal, actor_session_id)
        if execution and action not in READ_ACTIONS:
            if goal_id and goal_id != execution['goal_id']:
                raise board.BoardError('当前执行不能向另一目标派发修改')
            response = goals.discussion(conn, execution_id)
            if response and action != 'amend':
                raise board.Conflict('当前仅在回复已交付任务。用户确实要求修改时，先用 amend 登记修改内容；普通解释不重新执行任务。')
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id, allow_discussion=bool(response))
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
        if action == 'status':
            if goal:
                return {'goal': _status_goal(goal), 'executions': board._rows(conn,
                        'SELECT e.id,e.session_id,e.runtime,e.status,e.generation FROM goal_executions e '
                        'WHERE goal_id=? AND round=? AND generation=(SELECT MAX(x.generation) '
                        'FROM goal_executions x WHERE x.goal_id=e.goal_id AND x.round=e.round AND x.session_id=e.session_id) '
                        'ORDER BY e.session_id', (goal_id, goal['current_round'])),
                        'finish': goals.get_finish(conn, goal_id), 'work_items': goal_work.items(conn, goal_id)}
            rows = goals.list(conn, project, actor_session_id if actor and actor['role'] == 'leader' else None)
            if actor and actor['role'] != 'leader':
                rows = [item for item in rows if any(entry['session_id'] == actor_session_id for entry in item['assignments'])]
            return {'project': project, 'goals': [_status_goal(item) for item in rows]}
        if action == 'create':
            leader_id = data.get('leader_session_id') or data.get('leader') or (actor_session_id if actor and actor['role'] == 'leader' else None)
            if not leader_id:
                raise board.BoardError('创建目标必须指定主责 leader')
            leader = _person(conn, project, leader_id)
            missing = [key for key in ('title', 'request', 'acceptance') if key not in data]
            if missing:
                unknown = sorted(set(data) - CREATE_FIELDS - TRANSPORT_FIELDS)
                raise board.BoardError('create 缺少 data.' + '、data.'.join(missing) + '；' + CREATE_HELP
                                       + ('；不认识的字段：' + '、'.join(unknown) if unknown else ''))
            if data.get('start') is True:
                _host(controller)
            created = goals.create(conn, project, leader['session_id'], data.get('title'), data.get('request'),
                                   data.get('acceptance'), constraints=data.get('constraints', ()), priority=data.get('priority', 0),
                                   dependencies=data.get('dependencies', ()), actor=actor_session_id, source=data.get('source'),
                                   decision_mode=data.get('decision_mode'))
            if data.get('start') is not True:
                return created
            try:
                receipt = _start(conn, controller, created, leader, actor_session_id, execution_id=execution_id)
            except (board.BoardError, codex_client.CodexError, OSError) as exc:
                # The goal exists; say so, so a retry starts it instead of creating a duplicate.
                raise board.Conflict('任务已创建（%s，待开始），但没有开始：%s；处理后用 goal start %s 开始'
                                     % (created['id'], exc, created['id'])) from exc
            return {'goal': goals.get(conn, created['id']), 'receipt': receipt}
        if action == 'context.retrieve':
            from .storage_service import StorageService
            return StorageService(home).retrieve(project, {**data, 'goal_id':goal_id,
                'current_requirement_version':goal['requirement_version'] if goal else None,
                'current_goal_status':goal['status'] if goal else None},actor_session_id=actor_session_id if actor else None)
        if goal is None:
            raise board.BoardError('此操作必须指定目标')
        if action == 'report.preview':
            if set(data) - {'goal_id', 'project', 'document_html', 'report_id', 'viewport'}:
                raise board.BoardError('报告预览仅接受目标、报告正文或报告 ID 与视口，不接受路径、URL 或执行命令')
            if execution and execution['goal_id'] != goal_id:
                raise board.BoardError('报告预览必须属于当前执行目标')
            if ('document_html' in data) == ('report_id' in data):
                raise board.BoardError('报告预览必须且只能提供 document_html 或 report_id 其中一项')
            document = data.get('document_html')
            report_id = data.get('report_id')
            if report_id is not None:
                if not isinstance(report_id, str) or not report_id:
                    raise board.BoardError('报告预览 report_id 必须为非空报告 ID')
                report = goals.get_report(conn, report_id)
                if report['goal_id'] != goal_id:
                    raise board.BoardError('报告不属于当前任务')
                document = report['data'].get('document_html')
            host = _host(controller)
            previews = getattr(getattr(host, 'manager', None), 'report_previews', None)
            if previews is None:
                raise board.BoardError('当前 Crew 宿主不支持报告预览；尚未截图')
            return previews.capture(document, viewport=data.get('viewport', 'desktop'), title=goal['title'],
                                    goal_id=goal_id, report_id=report_id, actor_session_id=actor_session_id)
        if action in goal_artifacts.ACTIONS:
            return goal_artifacts.dispatch(conn, home, goal_id, action, data, actor=actor_session_id, execution_id=execution_id)
        if action == 'work.list':
            return {'items': goal_work.items(conn, goal_id, data.get('round')), 'summary': goal_work.summary(conn, goal_id)}
        if action == 'work.add':
            return goal_work.add(conn, goal_id, data.get('key'), data.get('title'), data.get('acceptance'),
                                 owner=data.get('owner_session_id'), dependencies=data.get('dependencies', ()), actor=actor_session_id)
        if action == 'work.update':
            return goal_work.update(conn, goal_id, data.get('key'), data.get('version'), actor=actor_session_id,
                                    **{key: data[key] for key in ('status', 'note', 'title', 'acceptance', 'dependencies') if key in data},
                                    owner=data.get('owner_session_id'))
        if action == 'report-template':
            return report_template(conn, goal_id)
        if action == 'report.get':
            identity = data.get('report_id')
            if identity is None:
                row = conn.execute('SELECT id FROM goal_reports WHERE goal_id=? ORDER BY round DESC,version DESC LIMIT 1', (goal_id,)).fetchone()
                identity = row['id'] if row else None
            report = goals.get_report(conn, identity)
            if report['goal_id'] != goal_id:
                raise board.BoardError('报告不属于当前任务')
            return {'report': report}
        if action == 'mode.set':
            return {'decision_mode': goal_mode.set_mode(conn, goal_id, data.get('mode'),
                    expected_version=data.get('expected_version'), actor=actor_session_id)}
        if action == 'report.publish':
            return goal_reports.publish(conn, goal_id, data.get('report'), request_id=data.get('request_id'),
                        actor=actor_session_id, execution_id=execution_id, source=data.get('source'),
                        expected_version=data.get('expected_version'))
        if action == 'context.inspect':
            return goal_context.inspect(conn, goal_id, execution_id)
        if action == 'context.put':
            return goal_context.put(conn, goal_id, data.get('kind', 'fact'), data.get('text'), data.get('source'),
                                    depends=data.get('depends', ()), confirmed=data.get('confirmed', False), actor=actor_session_id)
        if action in ('context.correct', 'context.expire'):
            item = goal_context.get(conn, data.get('item_id'))
            if item['goal_id'] != goal_id:
                raise board.BoardError('上下文条目不属于此目标')
            if action == 'context.correct':
                return goal_context.correct(conn, item['id'], data.get('text'), source=data.get('source'),
                                            depends=data.get('depends'), reason=data.get('reason', '纠正依据'), actor=actor_session_id)
            return goal_context.invalidate(conn, item['id'], data.get('reason'), actor=actor_session_id)
        goals._actor(conn, goal, actor_session_id)
        if action == 'amend':
            response = goals.discussion(conn, execution_id, include_promoted=True) if execution else None
            if response and response['promotion_json']:
                receipt = json.loads(response['promotion_json'])
                if all(receipt[key] == data.get(key) for key in ('text', 'acceptance', 'constraints', 'expected_version')):
                    return goals.get(conn, goal_id)
            if execution and goals.discussion(conn, execution_id):
                return goals.continue_task(conn, goal_id, data.get('text'), actor=actor_session_id,
                    discussion_execution_id=execution_id, acceptance=data.get('acceptance'), constraints=data.get('constraints'),
                    expected_version=data.get('expected_version'))
            if execution is None and actor_session_id != board.HUMAN and goal['status'] in DELIVERED:
                # A leader relaying the user's change after delivery continues this task in its own round and
                # never opens another round. (The user's own web supplements keep their existing path.)
                if not _user_turn(controller, actor_session_id):
                    raise board.Conflict('任务已交付；只有用户明确要求修改时才能继续。请在用户提出修改的那次对话里调用 amend，'
                                         '或请用户在任务里点「需修改」。')
                value = goals.continue_task(conn, goal_id, data.get('text'), actor=actor_session_id, user_requested=True,
                                            acceptance=data.get('acceptance'), constraints=data.get('constraints'),
                                            expected_version=data.get('expected_version'))
                return {**value, 'message': '已登记修改，任务仍在第 %s 轮、回到待开始，原工作清单沿用。用 Crew 工具 `goal_start`（`goal_id=%s`）'
                        '在本任务内接着改，完成并验证后重新用 `goal_finish` 提交验收。'
                        % (value['current_round'], goal_id)}
            amended = goals.amend(conn, goal_id, data.get('text'), acceptance=data.get('acceptance'),
                                  constraints=data.get('constraints'), actor=actor_session_id, expected_version=data.get('expected_version'))
            return _deliver_amendment(conn, amended, data['text'], actor_session_id, controller, execution_id)
        if action == 'assign':
            person = _person(conn, project, data.get('session_id') or data.get('to'))
            if data.get('start') is True:
                _host(controller)
                if goal['status'] != 'running':
                    raise board.Conflict('先启动目标；分配成员本身不隐式开工')
                controller.check_member_start(conn, goal_id, person['session_id'],
                                              'human' if actor_session_id == board.HUMAN else 'crew')
            assigned = goals.assign(conn, goal_id, person['session_id'], data.get('task'), actor=actor_session_id)
            if data.get('start') is True:
                receipt = controller.send(goal_id, person['session_id'], data['task'],
                                          identity=_start_identity(goal, person['session_id'], data['task']),
                                          source='human' if actor_session_id == board.HUMAN else 'crew')
                return {'goal': assigned, 'receipt': _started_work(conn, goal, person, actor_session_id, execution_id, receipt)}
            return assigned
        if action == 'start':
            _host(controller)
            person = _person(conn, project, data.get('session_id') or data.get('to') or goal['leader_session_id'])
            return _start(conn, controller, goal, person, actor_session_id, data.get('text'), execution_id=execution_id)
        if action == 'pause':
            return _host(controller).pause(goal_id, manual=actor_session_id == board.HUMAN, actor=actor_session_id)
        if action == 'resume':
            _host(controller)
            if not data.get('text'):
                review = goals.resume_review(conn, goal_id, actor=actor_session_id)
                if review is not None:
                    return {'ok': True, 'goal': review, 'resumed': 'acceptance'}
            goals.resume(conn, goal_id, actor=actor_session_id)
            finish = goals.get_finish(conn, goal_id)
            if finish and finish['status'] == 'pending':
                if data.get('text'):
                    goals.amend(conn, goal_id, data['text'], actor=actor_session_id)
                else:
                    goals.transition(conn, goal_id, 'running', actor=actor_session_id)
                    controller.sync(conn, goal_id)
                    return {'ok': True, 'goal': goals.get(conn, goal_id), 'resumed': 'finalization'}
            person = board.session(conn, goal['leader_session_id'])
            receipt = controller.send(goal_id, person['session_id'],
                                   data.get('text') or behavior_prompts.text(conn, 'resume_nudge', session_id=goal['leader_session_id'], values={'reason': '用户明确恢复本目标，按原始要求与已有证据续接'}),
                                   source='human' if actor_session_id == board.HUMAN else 'crew')
            return _started_work(conn, goal, person, actor_session_id, execution_id, receipt)
        if action == 'transfer':
            host = _host(controller)
            person = _person(conn, project, data.get('to') or data.get('leader') or data.get('leader_session_id') or '')
            transfer = goals.request_transfer(conn, goal_id, person['session_id'], data.get('text') or data.get('note') or '',
                                              actor=actor_session_id)
            host.sync(conn, goal_id)
            transfer = goals.get_transfer(conn, transfer['id'])
            if transfer['status'] == 'failed':
                raise board.Conflict('交接没有完成：' + transfer['error'])
            pending = transfer['status'] == 'pending'
            name = person['crew_name'] or person['session_id']
            message = ('已登记交给 %s；本任务正在进行的执行结束后自动交接，由 %s 接着做。不要再在本任务里施工，告诉用户已交接即可。' % (name, name)
                       if pending else '已交给 %s；%s 会收到交接通知和你的交接说明。你和你组里的成员不用再推进这个任务。' % (name, name))
            return {'ok': True, 'pending_transfer': pending, 'transfer': transfer, 'goal': _status_goal(goals.get(conn, goal_id)),
                    'message': message}
        if action == 'reopen':
            return goals.reopen(conn, goal_id, data.get('text'), actor=actor_session_id)
        if action == 'cancel':
            host = _host(controller)
            goals.request_cancel(conn, goal_id, actor=actor_session_id)
            host.sync(conn, goal_id)
            result = goals.finalize_cancel(conn, goal_id)
            return {'ok': True, 'pending_cancel': not result['finalized'], 'goal': goals.get(conn, goal_id)}
        if action == 'finish':
            _host(controller)
            with goals.transaction(conn):
                report = goals.save_report(conn, goal_id, data.get('report'))
                finish = goals.request_finish(conn, goal_id, report['id'], actor=actor_session_id, execution_id=execution_id)
            # Never interrupt this tool's own native caller or pretend it is idle.
            message = '报告已收录；本次原生响应实际结束后，程序按本次 outcome 自动收尾，无需重复提交。'
            if report['outcome'] == 'awaiting_acceptance':
                message += '收尾后进入等待用户验收；验收决定仍需用户本人提交，不能声称用户无需验收。'
            return {'ok': True, 'pending_finish': True, 'report_id': report['id'], 'finish': finish,
                    'message': message}
    raise board.BoardError('不支持的目标操作')
