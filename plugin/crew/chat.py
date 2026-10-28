"""Conversation coordination over carrier adapters and persistent session journals."""
import base64
import contextlib
from contextlib import closing
import functools
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path

from .session_journal import Journal
from .claude_events import compact_command
from .native_process import Running, child_environment, claude_executable, close_runtimes
from .execution_admission import WorkAdmission, UpdatePaused, PAUSED_FOR_UPDATE, MAINTENANCE_HELD
from .execution_host import ExecutionStore, NativeLauncher

from . import board, claude_limits, codex_client, codex_visibility, git_admin, goals, hooks, messaging, models, runtime_switch, runtimes, settings, spawn, store, workspace_execution


def _admitted(method):
    # Every entry that can start or feed a model turn passes the update gate.
    @functools.wraps(method)
    def admitted(self, *args, **kwargs):
        with self.work():
            return method(self, *args, **kwargs)
    return admitted


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def validate_images(images):
    if images is None:
        return []
    if not isinstance(images, list):
        raise board.BoardError("images 必须是图片列表")
    if len(images) > 5:
        raise board.BoardError("一次最多发送 5 张图片")
    for image in images:
        if (not isinstance(image, dict) or not isinstance(image.get("media_type"), str)
                or image["media_type"] not in IMAGE_TYPES):
            raise board.BoardError("图片只支持 image/png、image/jpeg、image/webp、image/gif")
        if not isinstance(image.get("data"), str):
            raise board.BoardError("图片 data 必须是 base64 字符串")
        try:
            raw = base64.b64decode(image["data"], validate=True)
        except ValueError as exc:
            raise board.BoardError("图片 data 不是有效的 base64") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise board.BoardError("单张图片解码后不能超过 5 MB")
    return images


class Manager:
    def __init__(self, home, plugin_root, agents_provider, process_factory=subprocess.Popen):
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider = agents_provider
        self.executions = ExecutionStore(home)
        self.native_launcher = NativeLauncher(self.executions, plugin_root, process_factory=process_factory,
                                              send_message=self.send_message, control=self._control,
                                              read=self._read, stderr=self._stderr)
        self._close_lock = threading.Lock()
        self.closed = False
        self.auto_candidates = None
        self.message_wait_errors = {}
        self._work_admission = WorkAdmission()
        from .goal_runtime import Controller
        self.goal_controller = Controller(self.executions, self._work_admission, self.native_launcher,
                                          send_input=self.send, stop=self.stop_managed,
                                          interrupt=self.interrupt, observe_claude=self._external_agents,
                                          scope_busy=self.scope_busy)

    @property
    def processes(self):
        return self.executions.processes

    @property
    def journals(self):
        return self.executions.journals

    @property
    def lock(self):
        return self.executions.lock

    @property
    def process_factory(self):
        return self.native_launcher.process_factory

    @process_factory.setter
    def process_factory(self, value):
        self.native_launcher.process_factory = value

    def work(self, background=False):
        return self._work_admission.work(background=background)

    def paused(self):
        return self._work_admission.paused()

    def close_admission(self, gate, timeout):
        return self._work_admission.close(gate, timeout)

    def open_admission(self, gate):
        return self._work_admission.open(gate)

    def busy_reasons(self, conn):
        """Real managed process state; pending compaction is queued work, too."""
        reasons = []
        with self.lock:
            for sid, current in self.processes.items():
                with current.input_lock:
                    alive = current.process.poll() is None
                    maintaining = alive and getattr(current, "maintenance", None) is not None
                    working = alive and (current.journal.status == "working" or current.pending_compact or maintaining)
                    draining = not alive and not current.reader_done.is_set()
                if working or draining:
                    row = conn.execute("SELECT crew_name FROM sessions WHERE session_id=?", (sid,)).fetchone()
                    name = row["crew_name"] if row is not None and row["crew_name"] else sid
                    reasons.append(("%s 正在进行部门维护压缩" if maintaining else "%s 正在回复或排队压缩" if working
                                    else "%s 的进程输出尚未排空") % name)
        return reasons

    def managed_pids(self):
        with self.lock:
            return [current.process.pid for current in self.processes.values() if current.process.poll() is None]

    def path(self, session_id):
        return self.executions.path(session_id)

    def journal(self, session_id):
        return self.executions.journal(session_id)

    def file(self, session_id, name):
        if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
            raise board.BoardError("图片文件名不能包含路径")
        journal = self.journal(session_id)
        with journal.condition:
            if not any(name in event.get("images", []) for event in journal.events if event["type"] == "message"):
                raise board.BoardError("该图片不在会话聊天记录中")
        media_type = next(media for media, suffix in IMAGE_TYPES.items() if name.endswith(suffix))
        return self.home / "chat" / "files" / session_id / name, media_type

    def running(self, session_id):
        return self.executions.running(session_id)

    def is_owned(self, session_id):
        return self.running(session_id)

    def owns_pid(self, session_id, pid):
        return self.executions.owns_pid(session_id, pid)

    def busy(self, session_id):
        return self.executions.busy(session_id)

    def scope_busy(self, key, runtime, native_id):
        """Check another host's ownership too, without starting a native process."""
        if self.running(key):
            return self.busy(key)
        if runtime == 'claude':
            return native_id is not None and any(agent['sessionId'] == native_id and agent.get('status') != 'idle'
                                                for agent in self._external_agents())
        journal = self.journal(key)
        with journal.condition:
            journal.refresh()
            if journal.status != 'working':
                return False
        from .codex_runtime import RuntimeLease
        try:
            lease = RuntimeLease(self.home, key).acquire()
            lease.release()
        except board.Conflict:
            return True
        except (OSError, ValueError) as exc:
            raise board.Conflict('无法确认另一个宿主的原生执行已退出，未启动新执行：' + str(exc)) from exc
        return False

    def maintenance_held(self, identity):
        return self.executions.maintenance_held(identity)

    def activity(self, session_id):
        return self.executions.activity(session_id)

    def applied(self, session_id):
        return self.executions.applied(session_id)

    def context(self, session_id):
        return self.executions.context(session_id)

    def history(self, session_id):
        return self.executions.history(session_id)

    def subagent_tasks(self, session_id):
        return self.executions.subagent_tasks(session_id)

    def _read(self, session_id, current):
        code = None
        try:
            current.events.bind(getattr(current, 'native_id', session_id))
            for line in current.process.stdout:
                try:
                    record = json.loads(line)
                    if (self.processes.get(session_id) is current and record.get('session_id') == (current.native_id if current.execution else session_id)
                            and not record.get('parent_tool_use_id')):
                        known_native = (record.get('type')=='assistant' or
                            record.get('type')=='system' and record.get('subtype')=='init')
                        receipt=current.pending_native_accept
                        acknowledged=(record.get('type')=='user' and receipt is not None and record.get('uuid')==receipt[0]
                                      and record.get('message',{}).get('role')=='user')
                        if known_native and not current.native_binding_confirmed or acknowledged:
                            try:
                                with closing(store.connect(self.home)) as native_conn:
                                    if current.execution:
                                        current.native_binding_confirmed = True
                                    elif runtimes.get(native_conn,session_id)['runtime']=='claude':
                                        if runtimes.get(native_conn,session_id)['native_id'] is None:
                                            runtimes.set(native_conn,session_id,'claude',session_id)
                                        current.native_binding_confirmed=True
                                        if acknowledged:
                                            runtime_switch.accepted(native_conn,session_id,receipt[0],receipt[1])
                                            current.pending_native_accept=None
                            except sqlite3.Error as exc:
                                # Keep draining native output and retain the handoff for
                                # the next accepted input rather than losing the reader.
                                current.journal.emit('error',text='原生会话确认记录失败，交接会保留重试：'+str(exc))
                    if (current.execution and record.get('type') == 'user' and record.get('session_id') == current.native_id
                            and record.get('message', {}).get('role') == 'user' and not record.get('parent_tool_use_id')
                            and record.get('uuid') in current.goal_manifests):
                        from . import goal_context
                        with closing(store.connect(self.home)) as goal_conn:
                            goal_context.mark_applied(goal_conn, current.goal_manifests.pop(record['uuid']),
                                                      execution_id=current.execution['id'])
                    if record["type"] == "control_response":
                        self._control_response(current, record["response"])
                    if record["type"] == "rate_limit_event":
                        try:
                            claude_limits.capture(self.home, record)
                        except OSError as exc:
                            current.journal.emit("error", text="Claude 额度记录失败：" + str(exc))
                    if not record.get("parent_tool_use_id") and (record["type"] == "result" or
                            record["type"] == "system" and record.get("subtype") == "compact_boundary"):
                        self._control(current, "get_context_usage")
                    if record["type"] == "result" and not record.get("parent_tool_use_id"):
                        with current.input_lock:
                            current.events.consume(record)
                            if current.pending_compact and current.process.poll() is None:
                                current.pending_compact = False
                                self._send(session_id, "/compact")
                    else:
                        current.events.consume(record)
                except (ValueError, KeyError, TypeError) as exc:
                    current.journal.emit("error", text="Claude 协议错误：" + str(exc))
            code = current.process.wait()
            current.stderr_reader.join(timeout=1)
            with current.input_lock:
                if code and current.stop_reason is None:
                    current.journal.emit("error", text="Claude 退出（%s）：%s" % (code, "\n".join(current.stderr)))
                elif not code and current.stop_reason is None and current.journal.status == "working":
                    current.journal.emit("error", text="Claude 会话连接已结束，本轮未完成")
        except (OSError, ValueError) as exc:
            current.journal.emit("error", text=str(exc))
        finally:
            try:
                with current.input_lock:
                    if current.journal.status == "working":
                        current.journal.emit("turn_result", status="interrupted" if current.stop_reason else "failed",
                                             success=False, reason=current.stop_reason or "process_exit", exit_code=code)
                    if current.pending_compact:
                        current.pending_compact = False
                        current.journal.emit("compact_cancelled", reason="会话进程已退出，取消这一轮结束后的压缩")
                    current.journal.set_status("offline")
            finally:
                current.reader_done.set()

    def _stderr(self, current):
        for line in current.process.stderr:
            current.stderr.append(line.rstrip())

    def _external_agents(self):
        # cli.claude_agents already validates the shape; only its failure needs translating.
        try:
            return self.agents_provider()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise board.Conflict("进程状态未知，请稍后再试：" + str(exc)) from exc

    def _runs_in_claude(self, session_id):
        with closing(store.connect(self.home)) as conn:
            return runtimes.get(conn, session_id)["runtime"] == "claude"

    def _check_offline(self, session):
        session_id = session["session_id"]
        if self.running(session_id):
            if not self._runs_in_claude(session_id):
                raise board.Conflict('它在后台在线，请用 crew send 发给 ' + session['crew_name'])
            raise board.Conflict("它在后台在线，直接用 SendMessage 发给 " + session["crew_name"])
        with closing(store.connect(self.home)) as conn:
            runtime = runtimes.get(conn, session_id)["runtime"]
            prior_claude=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='claude' AND native_id IS NOT NULL",(session_id,)).fetchone()
            prior_codex=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='codex' AND native_id IS NOT NULL",(session_id,)).fetchone()
        for agent in ([] if runtime == "codex" and not prior_claude else self._external_agents()):
            if agent["sessionId"] == session_id:
                if runtime=='claude':
                    raise board.Conflict("它在 VS Code 里在线，直接用 SendMessage 发给 " + agent["name"])
                raise board.Conflict("它的 Claude 会话在 VS Code 或终端里在线，请先关闭该外部会话")
        previous = self.processes.get(session_id)
        if previous is not None and not previous.reader_done.wait(10):
            raise board.BoardError("上一轮会话进程的输出尚未结束，请稍后查看状态")
        if runtime=='claude' and prior_codex:
            from .codex_runtime import RuntimeLease
            lease=RuntimeLease(self.home,session_id).acquire()
            lease.release()

    def _launch(self, session, spec, resume=False):
        try:
            with closing(store.connect(self.home)) as conn:
                session = workspace_execution.execution_session(conn, session)
                spec = {**spec, 'cwd': session['cwd'], 'project': session['project'],
                        '_workspace_notice': workspace_execution.migration_notice(conn, session)}
            self._claim_identity(session)
            return self._launch_native(session,spec,resume=resume)
        except Exception as exc:
            with closing(store.connect(self.home)) as conn:
                runtime_switch.failed(conn,session['session_id'],exc)
            raise

    def _identity_keys(self, conn, session_id):
        return [session_id] + [row['id'] for row in conn.execute(
            'SELECT id FROM goal_executions WHERE session_id=?', (session_id,))]

    def _claim_identity(self, session):
        """Only replace an older generation of this task, never another scope."""
        sid, key = session['session_id'], session.get('_storage_id', session['session_id'])
        execution = session.get('_execution')
        with self.lock, closing(store.connect(self.home)) as conn:
            if self.maintenance_held(sid):
                raise board.Conflict(MAINTENANCE_HELD)
            if messaging.stopped(conn, sid):
                raise board.Conflict('用户已停止此会话，请明确继续')
            if execution is None:
                return
            for row in conn.execute('SELECT id FROM goal_executions WHERE session_id=? AND goal_id=?',
                                    (sid, execution['goal_id'])):
                previous = row['id']
                if previous == key or not self.running(previous):
                    continue
                self.stop_managed(previous, reason='identity_handoff')
                self.journal(previous).emit('identity_handoff', session_id=sid, target_execution=key,
                    text='本任务旧执行已停止，使用新的执行继续；其他任务和总对话不受影响。')
                if self.auto_candidates is not None:
                    self.auto_candidates.pop(previous, None)
                self.goal_controller.sync(conn, execution['goal_id'])

    def _launch_native(self, session, spec, resume=False, maintenance=None):
        if '_execution' in session:
            if maintenance is not None:
                return self.goal_controller.launch_maintenance(session, spec, maintenance)
            return self.goal_controller.launch(session, spec)
        session_id = session["session_id"]
        with closing(store.connect(self.home)) as conn:
            from . import personal
            if personal.is_assistant(conn, session_id) and conn.execute(
                    'SELECT 1 FROM session_runtimes WHERE session_id=?', (session_id,)).fetchone() is None:
                selected = settings.resolve(conn, purpose='assistant', project=session['project'], session_id=session_id)
                runtimes.set(conn, session_id, selected['runtime'])
                models.set(conn, session_id, selected['model'], selected['effort'], runtime=selected['runtime'])
                if selected.get('resolution') is not None:
                    detail = {'role': 'assistant', 'selection': {key: selected[key] for key in ('runtime', 'model', 'effort')},
                              'resolution': selected['resolution']}
                    board._event(conn, 'runtime:selected', session['project'], session_id, detail, None)
                    self.journal(session_id).emit('runtime_selection', **detail)
        if resume:
            self._check_offline(session)
        with closing(store.connect(self.home)) as conn:
            options = models.get(conn, session_id)
            runtime = runtimes.get(conn, session_id)["runtime"]
            native_id=runtimes.get(conn,session_id)['native_id']
            pending=runtime_switch.pending(conn,session_id)
            from . import personal
            prompt_role = 'assistant' if personal.is_assistant(conn, session_id) else session['role']
            permission_mode = settings.effective(conn, project=session['project'], session_id=session_id,
                purpose=prompt_role if prompt_role in ('leader', 'member', 'assistant') else 'default')['execution']['claude']
        native_events = {'message', 'assistant', 'text', 'tool_result', 'input_delivery', 'turn_result',
                         'identity_handoff', 'subagent', 'compact', 'compact_request'}
        fresh_native = not any(event['type'] in native_events for event in self.journal(session_id).events)
        if resume and native_id is None and not fresh_native and (pending is None or pending['to_runtime']!=runtime):
            raise board.BoardError('原生会话 ID 丢失，不能猜测恢复或偷偷新建')
        native_resume=resume and native_id is not None
        if runtime == "codex":
            self.native_launcher.codex(session, spec, options, resume=native_resume,
                                        goal_handler=self.goal_controller.agent_action, maintenance=maintenance)
            return spawn.command(session_id, session["crew_name"], session["role"], runtime="codex", **options)
        exe = claude_executable()
        if native_id not in (None,session_id):
            raise board.BoardError('Claude 原生身份与 Crew 身份不一致，不能恢复错误会话')
        command = spawn.command(session_id, session["crew_name"], session["role"], native_resume, **options, agent=spec["agent"],
                                permission_mode=permission_mode, plugin_root=self.plugin_root)
        if spec.get('_workspace_notice'):
            command += ['--append-system-prompt', spec['_workspace_notice']]
        command[0] = exe
        journal = self.journal(session_id)
        identity = {**spec, "name": session["crew_name"], "role": session["role"], "group": session["group_name"]}
        environment = child_environment(identity)
        environment.update(CREW_SESSION_ID=session_id, CREW_HOME=str(self.home.resolve()))
        environment['PATH'] = str(self.home.resolve() / 'bin') + os.pathsep + environment.get('PATH', '')
        self.native_launcher.claude(command, key=session_id, cwd=session["cwd"],
                                    environment=environment, maintenance=maintenance, behavior_role=prompt_role)
        return command

    @_admitted
    def start(self, conn, project_name, name, task=None, group=None, role="member", members=(), cwd=None,
              new_worktree=None, base=None, now=None, member_session_ids=(), model=models.INHERIT, effort=models.INHERIT, agent=None,
              runtime=models.INHERIT, caller_session_id=None, caller_execution_id=None, goal_id=None):
        with self.lock:
            bound_goal = None
            if caller_execution_id is not None and caller_session_id is None:
                raise board.BoardError('目标派生人员必须明确调用身份')
            if caller_session_id is not None:
                caller = board.session(conn, caller_session_id)
                if caller['project'] != project_name or caller['role'] != 'leader' or caller['status'] == 'closed' or board.is_archived(conn, caller_session_id):
                    raise board.BoardError('只有本项目当前 leader 可以创建组员')
                own_group = conn.execute('SELECT name FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL',
                                         (project_name, caller_session_id)).fetchone()
                if own_group is None or role != 'member' or group not in (None, own_group['name']):
                    raise board.BoardError('leader 只能在自己的当前组创建成员')
                group = own_group['name']
                if caller_execution_id is not None:
                    execution = goals.get_execution(conn, caller_execution_id)
                    goal = goals.get(conn, execution['goal_id'], project_name)
                    if execution['session_id'] != caller_session_id or goal['leader_session_id'] != caller_session_id:
                        raise board.BoardError('调用执行不属于此目标的 leader')
                    gate = goals.dispatch_check(conn, goal['id'], execution_id=caller_execution_id)
                    if not gate['allowed'] or not self.running(caller_execution_id):
                        raise board.Conflict(gate['reason'] or '调用目标执行没有在当前宿主运行')
                    bound_goal = goal['id']
            if goal_id is not None:
                goal = goals.get(conn, goal_id, project_name)
                if role != 'member':
                    raise board.BoardError('只有成员可以通过 spawn --goal 绑定目标')
                if caller_session_id is not None:
                    goals._actor(conn, goal, caller_session_id)
                if bound_goal is not None and bound_goal != goal_id:
                    raise board.BoardError('创建成员的目标与调用执行不一致')
                gate = goals.dispatch_check(conn, goal_id)
                if not gate['allowed']:
                    raise board.Conflict(gate['reason'])
                bound_goal = goal_id
                leader = board.session(conn, goal['leader_session_id'])
                if group not in (None, leader['group_name']):
                    raise board.BoardError('成员分组必须属于目标的主责 leader')
                group = leader['group_name']
            if role == "leader":
                plan = spawn.leader_plan(conn, self.home, self.plugin_root, project_name, name, members, task,
                                         cwd=cwd, new_worktree=new_worktree, base=base, now=now,
                                         member_session_ids=member_session_ids, model=model, effort=effort, group=group, agent=agent, runtime=runtime)
            else:
                plan = spawn.prepare(conn, self.home, self.plugin_root, project_name, name, task, group, role,
                                     cwd=cwd, new_worktree=new_worktree, base=base, now=now, model=model, effort=effort, runtime=runtime)
            session_id = plan["session_id"]
            spec = plan["spec"]
            goal_receipt = {}

            def launch(_):
                worktree, branch = hooks.git_details(spec["cwd"])
                session = board.register_session(conn, session_id, project_name, spec["cwd"], worktree, branch, name=name,
                                                 role=spec["role"], group_name=spec["group"], spawn_id=spec["id"],
                                                 permission_mode="bypassPermissions", now=now)
                board.update_cc_name(conn, session_id, name, now=now)
                chosen_runtime = spec.get("runtime", "claude")
                runtimes.set(conn, session_id, chosen_runtime, session_id if chosen_runtime == "claude" else None, now=now)
                models.set(conn, session_id, spec["model"], spec["effort"], now=now)
                try:
                    with self.lock:
                        if bound_goal is not None:
                            goals.assign(conn, bound_goal, session_id, task, actor=caller_session_id)
                            goal_receipt.update(self.goal_controller.send(bound_goal, session_id, task, source='crew', intent='work'))
                            return
                        plan["command"] = self._launch(session, spec)
                        text = (spec["task"] if chosen_runtime == "codex" else "Read the file " + spec["md"] +
                                " with the Read tool and follow it. It is your task brief from Crew.")
                        self._send(session_id, text, spec["task"], source="crew")
                except Exception:
                    live = [key for key in self._identity_keys(conn, session_id) if self.running(key)]
                    status = ('working' if any(self.busy(key) for key in live) else 'idle') if live else 'offline'
                    board.set_status(conn, session_id, status, now=now)
                    raise

            result = spawn._execute(conn, plan, launch, members, member_session_ids)
            return {**result, **goal_receipt, "session_id": session_id, "role": spec["role"]}

    def _send(self, session_id, text, display_text=None, source='human', identity=None, images=None, sender=None):
        current = self.processes[session_id]
        logical = current.execution['session_id'] if current.execution is not None else session_id
        if self.maintenance_held(logical):
            raise board.Conflict(MAINTENANCE_HELD)
        identity = identity or str(uuid.uuid4())
        original_text=text if display_text is None else display_text
        handoff_id=None
        prompt_manifest = None
        prompt_digest = None
        git_revision = getattr(current, 'git_context_revision', None)
        if text.strip() != '/compact':
            with closing(store.connect(self.home)) as handoff_conn:
                git_revision, git_context = git_admin.input_context(handoff_conn, logical, git_revision, self.plugin_root)
                if current.execution is None:
                    from . import behavior_prompts, personal
                    person = board.session(handoff_conn, session_id)
                    role = 'assistant' if personal.is_assistant(handoff_conn, session_id) else getattr(current, 'behavior_role', None) or person['role']
                    role = role if role in ('leader', 'member', 'assistant') else 'member'
                    prompt_keys = ('common', 'assistant', 'context') if role == 'assistant' else ('common', role, 'report', 'context')
                    resolved_prompts = behavior_prompts.resolve(handoff_conn, project=person['project'], session_id=session_id,
                                                               keys=prompt_keys)
                    prompt_digest = hashlib.sha256(json.dumps(resolved_prompts, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                    if getattr(current, 'behavior_prompt_digest', None) != prompt_digest:
                        prompt_manifest = resolved_prompts
                        text = behavior_prompts.render(prompt_manifest) + '\n\n' + text
                    if role == 'assistant':
                        text = (personal.assistant_context(handoff_conn, session_id)['instructions'] + '\n'
                                '本次输入消息标识：' + identity + '；真实来源：' + source + '。'
                                '个人工具引用该message_id，宿主会核对实际用户原话；AI消息不授予用户权限。\n\n' + text)
                    text,handoff_id=runtime_switch.input_text(handoff_conn,session_id,text,identity)
                    summary = self.goal_controller.general_context(handoff_conn, session_id)
                    if summary:
                        text = summary + '\n\n' + text
            if git_context:
                text = git_context + '\n\n' + text
        files = []
        content = text
        if images:
            directory = self.home / "chat" / "files" / session_id
            directory.mkdir(parents=True, exist_ok=True)
            file_stem = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            for index, image in enumerate(images, 1):
                # Transport identities stay unchanged in receipts and journals;
                # a caller-controlled ID must never become a filesystem path.
                name = file_stem + "-" + str(index) + IMAGE_TYPES[image["media_type"]]
                (directory / name).write_bytes(base64.b64decode(image["data"], validate=True))
                files.append(name)
            content = [{"type": "image", "source": {"type": "base64", "media_type": image["media_type"],
                                                     "data": image["data"]}} for image in images]
            content.append({"type": "text", "text": text})
        if getattr(current, "runtime", "claude") == "codex":
            try:
                result=current.send(text, identity, display_text=original_text, source=source,
                                    images=images, sender=sender, image_files=files)
                current.git_context_revision = git_revision
                if handoff_id is not None:
                    with closing(store.connect(self.home)) as handoff_conn:
                        runtime_switch.accepted(handoff_conn,session_id,identity,handoff_id)
                if prompt_manifest:
                    with closing(store.connect(self.home)) as prompt_conn:
                        behavior_prompts.record_applied(prompt_conn, session_id, prompt_manifest, status='sent', identity='prompt:' + session_id + ':' + identity)
                    current.behavior_prompt_digest = prompt_digest
                return result
            except Exception as exc:
                with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
                raise
        message = {"type": "user", "uuid": identity,
                   "session_id": current.native_id if current.execution else session_id, "parent_tool_use_id": None,
                   "message": {"role": "user", "content": content}}
        try:
            with current.input_lock:
                if compact_command(original_text):
                    current.journal.compact_request(identity)
                else:
                    current.journal.message(identity, original_text, source=source, images=files, sender=sender)
                current.pending_native_accept=(identity,handoff_id) if handoff_id is not None else None
                current.journal.set_status("working")
                current.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                current.process.stdin.flush()
                current.git_context_revision = git_revision
                if prompt_manifest:
                    with closing(store.connect(self.home)) as prompt_conn:
                        behavior_prompts.record_applied(prompt_conn, session_id, prompt_manifest, status='sent', identity='prompt:' + session_id + ':' + identity)
                    current.behavior_prompt_digest = prompt_digest
        except Exception as exc:
            with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
            raise
        return identity

    def prepare_runtime(self,conn,session_id,runtime,catalog,model=None,effort=None,handoff=None,now=None):
        with self.lock:
            return runtime_switch.prepare(conn,board.session(conn,session_id),self,runtime,catalog,model,effort,handoff,now)

    def switch_runtime(self,conn,session_id,preview_id,confirm,catalog,now=None):
        with self.lock:
            return runtime_switch.switch(conn,board.session(conn,session_id),self,preview_id,confirm,catalog,now)

    def send_message(self, sender_id, target_id, text, message_id=None, execution_id=None, kind='information', reply_to=None):
        with self.lock, closing(store.connect(self.home)) as conn:
            if execution_id or reply_to is not None:
                from . import goal_messages
                return goal_messages.queue(conn, sender_id, target_id, text, execution_id,
                                           message_id=message_id, kind=kind, reply_to=reply_to)
            return messaging.queue(conn, sender_id, target_id, text, message_id=message_id, kind=kind)

    def deliver_messages(self):
        """Deliver only to an idle owned runtime, or resume a confirmed offline one."""
        with self.work(background=True) as admitted:
            if admitted:
                self._deliver_messages()

    def _deliver_messages(self):
        with self.lock, closing(store.connect(self.home)) as conn:
            from . import goal_messages
            from . import goal_links
            goal_links.deliver(self.goal_controller, conn)
            goal_messages.deliver(self.goal_controller, conn)
            for message in messaging.pending(conn):
                sid = message["target_session_id"]
                target = board.session(conn, sid)
                if target["status"] == "closed" or board.is_archived(conn, sid):
                    conn.execute("UPDATE peer_messages SET error=? WHERE id=?", ("目标已归档或移除", message["id"]))
                    continue
                if messaging.stopped(conn, sid):
                    continue
                if self.busy(sid):
                    continue
                sender = board.session(conn, message["sender_session_id"])
                body = messaging.text(conn, message)
                try:
                    if not self.path(sid).exists():
                        self.journal(sid).set_status('idle')
                    result = self.send(target, body, display_text=message['body'], source='peer',
                                       identity='peer:' + message['id'], sender=sender['crew_name'])
                    if not isinstance(result, dict) or result.get('accepted') is not True:
                        raise board.Conflict('执行输入尚无确认的接收回执')
                    messaging.mark_delivered(conn, message["id"], accepted=True)
                    self.message_wait_errors.pop(message["id"], None)
                except (board.BoardError, codex_client.CodexError, OSError, ValueError) as exc:
                    # External CC sessions receive through their next hook. Keep
                    # mail durable when liveness is unknown; never start a copy.
                    reason = type(exc).__name__ + ': ' + str(exc)
                    storage_id = sid
                    if self.path(storage_id).exists():
                        previous = self.journal(storage_id).inputs.get('peer:' + message['id'])
                        if previous and previous.get('status') == 'unknown' and previous.get('detail'):
                            original = previous.get('error', 'InputReceiptUnknown') + ': ' + previous['detail']
                            if original != reason:
                                reason = original + '；当前状态：' + reason
                    if self.message_wait_errors.get(message['id']) != reason:
                        self.message_wait_errors[message["id"]] = reason
                        conn.execute('UPDATE peer_messages SET error=? WHERE id=?',
                                     ('DeliveryWaiting: ' + reason, message['id']))
                        if not isinstance(exc, board.Conflict):
                            board.record_event(conn, "peer:delivery_wait", target["project"], sid,
                                               {"id": message["id"], "reason": reason})

    @_admitted
    def send(self, session, text, source='human', identity=None, images=None, display_text=None, sender=None):
        images = validate_images(images)
        session_id = session.get('_storage_id', session["session_id"])
        identity = str(uuid.uuid4()) if identity is None else identity
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 160:
            raise board.BoardError("消息标识必须是非空文本，最多 160 个字符")
        digest = hashlib.sha256(json.dumps({'text': text if display_text is None else display_text,
                                          'images': images, 'source': source},
                                          ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        with self.lock:
            if self.maintenance_held(session['session_id']):
                raise board.Conflict(MAINTENANCE_HELD)
            with closing(store.connect(self.home)) as routing_conn:
                session = workspace_execution.execution_session(routing_conn, session)
            if self.closed:
                raise board.BoardError("网页服务正在关闭")
            if session["status"] == "closed":
                raise board.Conflict("会话已归档，不能继续发送消息")
            if not self.path(session_id).exists():
                raise board.BoardError("该会话没有网页聊天记录")
            journal = self.journal(session_id)
            prior = journal.inputs.get(identity)
            if prior is not None:
                if prior['digest'] != digest:
                    raise board.Conflict("同一消息标识不能发送不同内容")
                if prior['status'] == 'accepted':
                    return {'ok': True, 'message_id': identity, 'accepted': True,
                            'delivery': prior['delivery'], 'duplicate': True,
                            **({'selection_resolution': prior['selection_resolution']} if 'selection_resolution' in prior else {})}
                if prior['status'] != 'rejected':
                    raise board.Conflict("上次输入的原生接收状态未确认，请先核对会话，避免重复执行")
            elif identity in journal.messages:
                raise board.Conflict("这条历史输入没有可确认的接收回执，请先核对会话")
            if '_execution' not in session:
                with closing(store.connect(self.home)) as routing_conn:
                    if messaging.stopped(routing_conn, session['session_id']):
                        if source not in ('human', 'qq', 'lark'):
                            raise board.Conflict('用户已停止此会话；排队消息不能自动恢复它')
                        messaging.clear_stop(routing_conn, session['session_id'])
            if '_execution' not in session and session['role'] == 'member' and source not in ('human', 'qq', 'lark'):
                with closing(store.connect(self.home)) as check_conn:
                    active = next((row['id'] for row in check_conn.execute(
                        'SELECT e.id,e.runtime,b.native_id FROM goal_executions e LEFT JOIN goal_execution_bindings b '
                        'ON b.execution_id=e.id AND b.runtime=e.runtime WHERE e.session_id=?', (session_id,))
                        if self.scope_busy(row['id'], row['runtime'], row['native_id'])), None)
                if active is not None:
                    raise board.Conflict('成员的目标执行正在工作，不能向总对话重复派工；'
                                         '请明确目标继续原执行或等待当前任务结束：' + active)
            steering = self.running(session_id) and self.journal(session_id).status == "working"
            if steering and source not in ("human", "qq", "lark"):
                raise board.Conflict("会话正在回复，自动消息等待当前轮次结束")
            if not self.running(session_id):
                with closing(store.connect(self.home)) as spec_conn:
                    spec, _ = self._resume_spec(spec_conn, session)
                self._launch(session, spec, resume=True)
            delivery = 'steered' if steering else 'started'
            selection = next(({'selection_resolution': event['resolution']} for event in reversed(journal.events)
                if event['type'] == 'runtime_selection' and event['selection']['runtime'] == self.processes[session_id].runtime), {})
            journal.emit('input_delivery', message_id=identity, digest=digest, status='submitted', delivery=delivery)
            try:
                identity = self._send(session_id, text, display_text=display_text,
                                      source=source, identity=identity, images=images, sender=sender)
            except (codex_client.RpcError, board.BoardError) as exc:
                journal.emit('input_delivery', message_id=identity, digest=digest, status='rejected',
                             delivery=delivery, error=type(exc).__name__, detail=str(exc))
                raise
            except (codex_client.TransportError, codex_client.RequestTimeout, OSError) as exc:
                journal.emit('input_delivery', message_id=identity, digest=digest, status='unknown',
                             delivery=delivery, error=type(exc).__name__, detail=str(exc))
                raise
            journal.emit('input_delivery', message_id=identity, digest=digest, status='accepted', delivery=delivery, **selection)
        return {"ok": True, 'message_id': identity, 'accepted': True,
                'delivery': delivery, **selection}

    @_admitted
    def compact(self, session, caller_session_id=None, after_turn=False):
        session_id = session["session_id"]
        self_compact = caller_session_id not in (None, board.HUMAN) and caller_session_id == session_id
        deferred = self_compact or after_turn and caller_session_id in (None, board.HUMAN)
        with self.lock:
            if caller_session_id not in (None, board.HUMAN):
                with closing(store.connect(self.home)) as conn:
                    leader = conn.execute(
                        "SELECT s.* FROM groups g JOIN sessions s ON s.session_id=g.leader_session_id "
                        "WHERE g.project=? AND g.name=? AND g.closed_at IS NULL AND s.session_id=? "
                        "AND s.role='leader' AND s.status!='closed' AND s.project=g.project",
                        (session["project"], session["group_name"], caller_session_id)).fetchone()
                    if leader is None or board.is_archived(conn, caller_session_id):
                        raise board.BoardError("只有它自己的 leader 或用户能压缩它")
                if self_compact and not self.running(session_id):
                    raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            running = self.running(session_id)
            if running:
                if self.busy(session_id) and not deferred:
                    raise board.Conflict("正在干活，等这一轮结束再压缩")
            elif self._runs_in_claude(session_id) and any(agent["sessionId"] == session_id for agent in self._external_agents()):
                raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            if (not self.path(session_id).exists() or session["spawn_id"] is None or
                    not (self.home / "spawn" / (session["spawn_id"] + ".json")).exists()):
                raise board.BoardError("会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact")
            if deferred and (self_compact or running):
                current = self.processes[session_id]
                with current.input_lock:
                    if current.process.poll() is not None:
                        raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
                    if self.closed:
                        raise board.BoardError("网页服务正在关闭")
                    if self.maintenance_held(session_id):
                        raise board.Conflict(MAINTENANCE_HELD)
                    if (current.journal.progress or {}).get('phase') == 'compacting':
                        return {"ok": True, "message": "正在压缩上下文"}
                    if current.journal.status == "working":
                        current.pending_compact = True
                        return {"ok": True, "message": "这一轮说完后压缩", **({'queued': True} if after_turn else {})}
                    return {"ok": True, "message_id": self._send(session_id, "/compact")}
            return self.send(session, "/compact")

    def maintenance_launch(self, session_id, execution, marker):
        """Resume one existing native history solely for compaction.

        The caller has confirmed this person has no other working native.
        Other idle conversations stay resident; business input for this person
        is held until the fixed maintenance command finishes.
        Unlike _launch this never claims the identity (nothing else is paused or
        stopped), never creates a spawn record or native binding, and never
        consults goal dispatch: a finished or paused goal keeps its state.
        """
        with self.lock:
            with closing(store.connect(self.home)) as conn:
                target = self.goal_controller.target(conn, execution) if execution is not None else board.session(conn, session_id)
                target = workspace_execution.execution_session(conn, target)
                if target['spawn_id'] is None or not (self.home / 'spawn' / (target['spawn_id'] + '.json')).is_file():
                    raise board.BoardError('没有启动记录，不能恢复原生会话')
                spec = {**json.loads((self.home / 'spawn' / (target['spawn_id'] + '.json')).read_text(encoding='utf-8')),
                        'cwd': target['cwd'], 'project': target['project'],
                        '_workspace_notice': workspace_execution.migration_notice(conn, target)}
            self._launch_native(target, spec, resume=True, maintenance=marker)
            return self.processes[execution['id'] if execution is not None else session_id]

    def maintenance_compact(self, key, identity):
        """Submit only the native compact command to a maintenance-held process.

        Returns the journal position before the request; completion is observed
        from later native events, never from this submission.
        """
        with self.lock:
            current = self.processes.get(key)
            if getattr(current, 'maintenance', None) is None or not self.running(key):
                raise board.Conflict('维护压缩没有持有运行中的原生进程')
            journal = current.journal
            with current.input_lock:
                with journal.condition:
                    journal.refresh()
                    start = journal.events[-1]['seq'] if journal.events else 0
                journal.compact_request(identity)
                if getattr(current, 'runtime', 'claude') == 'codex':
                    current.compact()
                    return start
                journal.set_status('working')
                current.process.stdin.write(json.dumps({
                    'type': 'user', 'uuid': identity, 'session_id': current.native_id if current.execution else key,
                    'parent_tool_use_id': None, 'message': {'role': 'user', 'content': '/compact'}}) + '\n')
                current.process.stdin.flush()
            return start

    def maintenance_release(self, key, current):
        """Stop a native that maintenance started; a pre-existing process is only unmarked."""
        with self.lock:
            if current.maintenance['owned'] and self.processes.get(key) is current:
                # The marker stays if stopping fails, so nothing else can feed it.
                self.stop_managed(key)
                self.executions.remove(key, current)
            current.maintenance = None

    def _resume_spec(self, conn, session, now=None):
        supplied_session = session
        session = workspace_execution.execution_session(conn, session)
        session_id = session["session_id"]
        if session["spawn_id"] is not None:
            path = self.home / "spawn" / (session["spawn_id"] + ".json")
            return {**json.loads(path.read_text(encoding="utf-8")), 'cwd': session['cwd'], 'project': session['project']}, False
        spawn_id = uuid.uuid4().hex
        spec = {"id": spawn_id, "name": session["crew_name"], "group": session["group_name"],
                "role": session["role"], "task": session["task"], "cwd": session["cwd"],
                "project": session["project"], "agent": "crew:leader" if session["role"] == "leader" else None,
                "md": None, "created_at": store.now_iso() if now is None else now,
                "runtime": runtimes.get(conn, session_id)["runtime"], **models.get(conn, session_id)}
        path = self.home / "spawn" / (spawn_id + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        conn.execute("UPDATE sessions SET spawn_id=? WHERE session_id=?", (spawn_id, session_id))
        session["spawn_id"] = spawn_id
        supplied_session['spawn_id'] = spawn_id
        return spec, True

    def _resume(self, conn, session, text, sender=None, now=None, identity=None, source="crew", display_text=None):
        previous_spawn = session['spawn_id']
        journal = self.journal(session["session_id"])
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        journal.path.touch(exist_ok=True)
        board.update_cc_name(conn, session["session_id"], session["crew_name"], now=now)
        self.send(session, text, source=source, sender=sender, identity=identity, display_text=display_text)
        return previous_spawn is None and session['spawn_id'] is not None

    @_admitted
    def wake(self, conn, target, text, caller_session_id=None, now=None):
        with self.lock:
            target = workspace_execution.execution_session(conn, target)
            if self.closed:
                raise board.BoardError("网页服务正在关闭")
            control = board.session_control(conn, target["session_id"])
            if control and control["removed"]:
                raise board.BoardError("目标已移除，不能叫醒")
            if control and control["archived_status"] is not None:
                raise board.BoardError("目标已归档，不能叫醒")
            if target["status"] == "closed":
                raise board.BoardError("目标已收，不能叫醒")
            if target["crew_name"] is None:
                raise board.BoardError("目标未收编，不能叫醒")
            if target["role"] == "leader" and conn.execute(
                    "SELECT 1 FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL",
                    (target["project"], target["session_id"])).fetchone() is None:
                raise board.BoardError("目标已收，不能叫醒")
            leader = None
            if target["group_name"] is not None:
                group = conn.execute("SELECT leader_session_id FROM groups WHERE project=? AND name=? AND closed_at IS NULL",
                                     (target["project"], target["group_name"])).fetchone()
                if group is not None and group["leader_session_id"] is not None:
                    leader = board.session(conn, group["leader_session_id"])
            human = caller_session_id in (None, board.HUMAN)
            caller = None if human else board.session(conn, caller_session_id)
            if caller is not None:
                caller_group = conn.execute(
                    "SELECT 1 FROM groups WHERE project=? AND name=? AND leader_session_id=? AND closed_at IS NULL",
                    (caller["project"], caller["group_name"], caller_session_id)).fetchone()
                own_leader = board.leader_of(conn, caller_session_id) if caller["role"] == "member" else None
                member_to_leader = (caller["role"] == "member" and caller["crew_name"] is not None and
                                    target["role"] == "leader" and own_leader is not None and
                                    own_leader["session_id"] == target["session_id"])
                leader_to_session = caller["role"] == "leader" and caller_group is not None
                if (not (leader_to_session or member_to_leader) or caller["project"] != target["project"] or
                        caller["status"] == "closed" or board.is_archived(conn, caller_session_id)):
                    raise board.BoardError("只有同项目的 leader 或用户能叫醒它；组员只能叫醒自己的 leader")
            if target["role"] == "leader":
                if human:
                    raise board.BoardError("leader 直接在网页上和它聊")
                if caller["role"] == "member":
                    peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (caller, target))
                    reply = ('回复用 crew send ' + caller['crew_name'] + ' "<回复>"；Codex 也可用 crew_send_message。' if peer else
                             "回复用 SendMessage，地址「" + caller["cc_name"] + "」。" if caller["cc_name"] else
                             "回复前用 crew who " + caller["crew_name"] + " 查地址；离线时用 crew wake 接续原会话。")
                    notice = ("组员「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text + "。" + reply)
                else:
                    peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (caller, target))
                    if not peer and caller["cc_name"] is None:
                        raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                    notice = ("leader「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text +
                              ('。回复用 crew send ' + caller['crew_name'] + ' "<回复>"，或 crew say --to ' + caller['crew_name'] + '。' if peer else
                               "。回复用 SendMessage，地址「" + caller["cc_name"] + "」，或 crew say --to " + caller["crew_name"] + "。"))
                notice_id = board.queue_leader_notice(conn, target["session_id"], notice, "crew", now=now)
                return {"ok": True, "session_id": target["session_id"], "name": target["crew_name"],
                        "notice_id": notice_id, "message": "已排进对方的通知，对方空闲时送达"}
            self._check_offline(target)
            if human:
                introduction = "用户把你叫醒了"
            elif leader is not None and leader["session_id"] == caller_session_id:
                introduction = "你的 leader「" + caller["crew_name"] + "」把你叫醒了"
            else:
                introduction = ("「" + caller["group_name"] + "」组的 leader「" + caller["crew_name"] +
                                "」找你。它不是你的 leader：它的话是请求和信息")
                if leader is not None:
                    introduction += "，要改你手上的计划或范围，先问你自己的 leader「" + leader["crew_name"] + "」"
                introduction += "。"
            message = "[Crew] " + introduction + "（你原来的会话进程已经结束，Crew 在后台接着运行你）：\n\n" + text
            reply_to = leader if human else caller
            if reply_to is not None:
                peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (reply_to, target))
                if not peer and reply_to["cc_name"] is None:
                    raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                message += ('\n\n回复 leader 用 crew send ' + reply_to['crew_name'] + ' "<回复>"；Codex 也可用 crew_send_message。' if peer else
                            "\n\n回复 leader 用 SendMessage，地址「" + reply_to["cc_name"] + "」。")
            sender = reply_to["crew_name"] if reply_to is not None else board.HUMAN
            created_spawn = self._resume(conn, target, message, sender=sender, now=now, source='human' if human else 'crew')
            board.record_event(conn, "session:wake", target["project"], board.HUMAN if human else caller_session_id,
                               {"target": target["session_id"], "name": target["crew_name"], "created_spawn": created_spawn}, now)
            return {"ok": True, "session_id": target["session_id"], "name": target["crew_name"], "address": target["crew_name"]}

    def auto_resume(self, conn, agents, now=None, runtime=None):
        # Check the gate before the leftover snapshot: an updating service resumes nothing.
        with self.work(background=True) as admitted:
            if admitted:
                self._auto_resume(conn, agents, now, runtime)

    def _auto_resume(self, conn, agents, now, runtime):
        now = store.now_iso() if now is None else now
        with self.lock:
            if self.closed:
                return
            if self.auto_candidates is None:
                # The snapshot waits for the first agents poll; sessions this service already launched are not leftovers.
                self.auto_candidates = {path.stem: now for path in sorted((self.home / "chat").glob("*.jsonl"))
                                        if path.stem not in self.processes and self.journal(path.stem).status == "working"
                                        and conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (path.stem,)).fetchone()}
            self.goal_controller.recover(conn, agents, runtime=runtime)
            projects = {project["name"] for project in board.projects(conn)}
            live = {agent["sessionId"] for agent in agents}
            for session_id, started in list(self.auto_candidates.items()):
                if runtime is not None and runtimes.get(conn, session_id)['runtime'] != runtime:
                    continue
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                session = dict(row) if row is not None else None
                reason = None
                if session is None:
                    reason = "会话未登记"
                elif board.is_archived(conn, session_id):
                    reason = "会话已归档或移除"
                elif session["status"] == "closed":
                    reason = "会话已收"
                elif session["project"] not in projects:
                    reason = "所在项目未启用"
                elif messaging.stopped(conn, session_id):
                    reason = '用户已停止会话'
                elif self.running(session_id):
                    reason = "会话已由网页继续运行"
                elif session_id in live:
                    if (store.parse_iso(now) - store.parse_iso(started)).total_seconds() < 30 * 60:
                        continue
                    reason = "活进程在 30 分钟内未退出"
                else:
                    try:
                        self._resume(conn, session, "[Crew] Crew 服务刚重启，你上一轮做到一半被打断了。先看看做到哪了再接着做；如果其实已经做完，就照常收尾、按守则汇报。", now=now)
                    except board.Conflict as exc:
                        if (store.parse_iso(now) - store.parse_iso(started)).total_seconds() < 30 * 60:
                            continue
                        reason = str(exc)
                    except (OSError, subprocess.SubprocessError, board.BoardError) as exc:
                        reason = str(exc)
                board.record_event(conn, "session:auto-resume", session["project"] if session is not None else None,
                                   session_id, {"result": "abandoned" if reason is not None else "resumed",
                                                "reason": reason or "服务重启时上一轮仍在 working"}, now)
                del self.auto_candidates[session_id]

    def _control(self, current, subtype, **fields):
        identity = str(uuid.uuid4())
        request = {"type": "control_request", "request_id": identity, "request": {"subtype": subtype, **fields}}
        with current.input_lock:
            current.control_requests[identity] = subtype
            current.process.stdin.write(json.dumps(request) + "\n")
            current.process.stdin.flush()

    def _control_response(self, current, response):
        with current.input_lock:
            subtype = current.control_requests.pop(response["request_id"], None)
            if subtype == "get_settings" and response["subtype"] == "success":
                applied = response["response"]["applied"]
                current.applied = {"model": applied["model"], "effort": applied["effort"]}
                current.journal.emit('native_settings', runtime='claude', source='claude:get_settings', **current.applied)
            elif subtype == "get_context_usage" and response["subtype"] == "success":
                usage = response["response"]
                current.context = {"used": usage["totalTokens"], "max": usage["maxTokens"], "source": "live"}
        if subtype in ("set_model", "apply_flag_settings") and response["subtype"] == "success":
            self._control(current, "get_settings")

    def sync_native_titles(self, conn, session_id):
        session = board.session(conn, session_id)
        warnings, handled = [], set()
        with self.lock:
            for current in self.processes.values():
                if (getattr(current, 'runtime', None) == 'codex' and getattr(current, 'session_id', None) == session_id
                        and current.process.poll() is None and not current.client.closed):
                    warning = current.sync_title()
                    if warning:
                        warnings.append(warning)
                    handled.add(current.thread_id)
        pending = [native_id for native_id in codex_visibility.stored_person_threads(conn, session) if native_id not in handled]
        if pending:
            try:
                with codex_client.Client(process_factory=self.process_factory) as client:
                    for native_id in pending:
                        codex_visibility.set_title(client, native_id, session)
            except (codex_client.CodexError, OSError, ValueError) as exc:
                warnings.append('Codex 标题同步失败：' + str(exc))
        return warnings

    def set_options(self, conn, session_id, changes, now=None):
        with self.lock:
            if not changes:
                raise ValueError("至少提供 model 或 effort")
            runtime = runtimes.get(conn, session_id)["runtime"]
            if runtime == "codex":
                options = {**models.get(conn, session_id), **changes}
                models.validate_options(**options, runtime=runtime)
                if self.running(session_id):
                    self.processes[session_id].set_options(options)
                models.set(conn, session_id, **options, now=now)
                return options
            if "effort" in changes and changes["effort"] is None and self.running(session_id):
                raise ValueError("运行中的会话不能将 effort 恢复默认；请选择具体档位，或停止后设为 null 以在下次启动跟随账号设置")
            options = {**models.get(conn, session_id), **changes}
            models.validate_options(**options)
            models.set(conn, session_id, **options, now=now)
            if not self.running(session_id):
                return options
            current = self.processes[session_id]
            if "model" in changes:
                self._control(current, "set_model", **({"model": options["model"]} if options["model"] is not None else {}))
            if "effort" in changes:
                self._control(current, "apply_flag_settings", settings={"effortLevel": options["effort"]})
            return options

    def interrupt(self, session_id):
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                raise board.BoardError("网页服务没有管理这个会话进程")
            if not self.running(session_id):
                raise board.BoardError("会话没有在运行")
            if not self.busy(session_id):
                raise board.Conflict("会话当前空闲，没有正在进行的轮次")
            if current.execution is not None:
                self.goal_controller.interrupt_turn(current.execution['id'])
            if getattr(current, "runtime", "claude") == "codex":
                current.interrupt()
                return {"ok": True}
            request = {"type": "control_request", "request_id": str(uuid.uuid4()), "request": {"subtype": "interrupt"}}
            with current.input_lock:
                current.process.stdin.write(json.dumps(request) + "\n")
                current.process.stdin.flush()
        return {"ok": True}

    def stop_managed(self, session_id, *, reason='managed_stop'):
        """Terminate an owned process, including busy turns, and drain its output."""
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                return
            if getattr(current, "runtime", "claude") == "codex":
                with current.input_lock:
                    if current.process.poll() is None:
                        current.stop_reason = reason
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程已关闭，但输出尚未排空")
                return
            with current.input_lock:
                current.pending_compact = False
                if current.process.poll() is None:
                    current.stop_reason = reason
                    try:
                        current.process.terminate()
                    except OSError:
                        current.stop_reason = None
                        raise
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError('托管会话终止后仍未退出') from exc
            if not current.reader_done.wait(10):
                raise board.BoardError('托管会话进程已退出，但输出尚未读完')
            current.stderr_reader.join(timeout=10)
            if current.stderr_reader.is_alive():
                raise board.BoardError('托管会话进程已退出，但错误输出尚未读完')

    def stop_identity(self, session_id, caller_session_id=None):
        """Persist a stop gate, then confirm every owned native for this person has exited."""
        actor = board.HUMAN if caller_session_id is None else caller_session_id
        with self.lock, closing(store.connect(self.home)) as conn:
            target = board.session(conn, session_id)
            if actor != board.HUMAN:
                caller = board.session(conn, actor)
                leader = board.leader_of(conn, session_id)
                if caller['project'] != target['project'] or (actor != session_id and
                        (leader is None or leader['session_id'] != actor or caller['role'] != 'leader')):
                    raise board.BoardError('只能停止自己或自己组内的成员')
            conn.execute('INSERT INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES(?,?,?) '
                         'ON CONFLICT(session_id) DO UPDATE SET stopped_at=excluded.stopped_at,stopped_by=excluded.stopped_by',
                         (session_id, store.now_iso(), actor))
            keys = self._identity_keys(conn, session_id)
            owned_claude = {key: getattr(self.processes[key], 'native_id', key) for key in keys
                            if self.running(key) and getattr(self.processes[key], 'runtime', 'claude') == 'claude'}
            paused = set()
            if target['role'] == 'leader':
                for row in conn.execute("SELECT id FROM goals WHERE leader_session_id=? AND status NOT IN ('completed','failed','cancelled')",
                                        (session_id,)):
                    goals.request_pause(conn, row['id'], manual=True, actor=actor, reason='明确停止此人员')
                    paused.add(row['id'])
            for key in keys:
                if self.running(key) and self.busy(key):
                    try:
                        self.interrupt(key)
                    except (board.BoardError, codex_client.CodexError, OSError) as exc:
                        self.journal(key).emit('error', text='原生中断未确认，继续关闭托管进程：' + str(exc))
                self.stop_managed(key, reason='user_stop')
                if self.auto_candidates is not None:
                    self.auto_candidates.pop(key, None)
            external_claude = set()
            for key in keys:
                native_claude = set()
                if key == session_id:
                    mapping = runtimes.get(conn, key)
                    bindings = list(conn.execute('SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=?', (key,)))
                    if mapping['runtime'] == 'claude':
                        native_claude.add(mapping['native_id'] or key)
                else:
                    execution = goals.get_execution(conn, key)
                    mapping, bindings = execution, execution['bindings']
                native_claude.update(binding['native_id'] for binding in bindings
                                     if binding['runtime'] == 'claude' and binding['native_id'])
                external_claude.update(native_claude - {owned_claude.get(key)})
                if mapping['runtime'] == 'codex' or any(binding['runtime'] == 'codex' for binding in bindings):
                    from .codex_runtime import RuntimeLease
                    try:
                        lease = RuntimeLease(self.home, key).acquire()
                        lease.release()
                    except board.Conflict as exc:
                        raise board.Conflict('Codex 执行仍由外部宿主持有或原生进程仍存活，请到原工具停止后重试') from exc
                    except (OSError, ValueError) as exc:
                        raise board.Conflict('无法确认外部 Codex 执行已退出，请到原工具确认停止') from exc
            if external_claude:
                try:
                    agents = self._external_agents()
                except board.Conflict as exc:
                    raise board.Conflict('无法确认外部 Claude 执行已退出，请到原工具确认停止') from exc
                if any(agent['sessionId'] in external_claude for agent in agents):
                    raise board.Conflict('Claude 会话仍在外部运行，Crew 没有持有该进程，请到原工具停止后重试')
            for key in keys[1:]:
                if goals.get_execution(conn, key)['status'] not in goals.TERMINAL:
                    goals.set_execution_status(conn, key, 'paused')
            for goal_id in paused:
                self.goal_controller.sync(conn, goal_id)
            self.journal(session_id).emit('manual_stop', stopped_by=actor, text='已停止此人员的所有托管执行；排队消息不能自动恢复。')
            board.record_event(conn, 'session:stopped', target['project'], session_id, {'actor': actor, 'executions': keys[1:]})
            return {'ok': True, 'session_id': session_id, 'stopped': True, 'running': False,
                    'execution_ids': keys[1:]}

    def detach(self, session_id):
        """Release an idle session to VS Code by letting its own process finish."""
        with self.lock:
            if not self.is_owned(session_id):
                raise board.BoardError("网页服务没有管理这个会话进程")
            if self.busy(session_id):
                raise board.Conflict("会话正在回复，请等这一轮结束")
            current = self.processes[session_id]
            if getattr(current, "runtime", "claude") == "codex":
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程输出尚未排空")
                return
            with current.input_lock:
                if current.process.poll() is None:
                    current.stop_reason = 'detach'
                try:
                    current.process.stdin.close()
                except OSError:
                    current.stop_reason = None
                    raise
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError("会话关闭输入后仍未退出，请稍后查看进程状态") from exc
            if not current.reader_done.wait(10):
                raise board.BoardError("会话进程已退出，但输出尚未读完")

    def close(self):
        # Each native owns its own archive/transport deadlines. Waiting for one
        # under the router lock blocks callbacks and makes those waits additive.
        # Serialize repeated shutdown requests, but close independent carriers
        # outside that lock and retain every existing native wait/reader drain.
        with self._close_lock:
            with self.lock:
                self.closed = True
                closing_processes = list(self.processes.values())
                for journal in self.journals.values():
                    with journal.condition:
                        journal.condition.notify_all()

            close_runtimes(closing_processes)
