"""Persistent Claude stream-json sessions and their replayable web journals."""
import base64
import collections
import contextlib
from contextlib import closing
import datetime
import functools
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import behavior_prompts, runtime_recovery, trust_mode

from . import board, change_feed, channel, claude_limits, claude_subagents, codex_client, codex_visibility, git_admin, goal_messages, goals, guard, hooks, install, messaging, models, personal, report_preview, runtime_switch, runtimes, settings, spawn, store, subagents, workspace_execution
from .chat_base import (
    PAUSED_FOR_UPDATE, MAINTENANCE_HELD, REPLY_KIND, CLAUDE_NO_CONVERSATION, NATIVE_MISSING,
    NATIVE_REPLACED, NATIVE_LOST_INPUT, NATIVE_OUTPUT, IDLE_RECLAIM_MINUTES, idle_reclaim_seconds,
    track_background, native_output, has_native_output, lost_turn, UpdatePaused, _admitted,
    IMAGE_TYPES, MAX_IMAGE_BYTES, compact_command, tool_label, validate_images, child_environment,
)
from .chat_journal import clear_unstarted_member_bindings, Journal, Running
from .chat_status import StatusMixin
from .chat_native import NativeMixin
from .chat_delivery import DeliveryMixin
from .chat_maintenance import MaintenanceMixin
from .chat_resume import ResumeMixin
from .chat_personal import PersonalMixin
from .chat_control import ControlMixin
from .chat_lifecycle import LifecycleMixin


def claude_executable():
    exe = shutil.which("claude")
    if exe is None:
        raise FileNotFoundError("claude not found in PATH")
    if Path(exe).suffix.lower() == ".cmd":
        # The local npm shim starts a wrapper cmd.exe; agents reports the native child's PID.
        exe = str(Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    return exe


class Manager(StatusMixin, NativeMixin, DeliveryMixin, MaintenanceMixin,
              ResumeMixin, PersonalMixin, ControlMixin, LifecycleMixin):
    def __init__(self, home, plugin_root, agents_provider, process_factory=subprocess.Popen):
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.process_factory = agents_provider, process_factory
        self.journals, self.processes = {}, {}
        # Conversation state a workspace page shows; the service records these marks (docs/EVENT_STREAM_DESIGN.md §6.2).
        self.changes = change_feed.Notifier()
        self.context_history = None
        self.lock = threading.RLock()
        self._close_lock = threading.Lock()
        self.closed = False
        self.auto_candidates = None
        self.message_wait_errors = {}
        # Update admission: the gate closes new work; in_flight drains accepted work.
        self.admission = threading.Condition()
        self.update_gate, self.in_flight = None, 0
        self._admitted = threading.local()
        from .goal_runtime import Controller
        self.goal_controller = Controller(self)
        self.runtime_recovery = runtime_recovery.Controller(self)
        self.native_binding_cleanup = clear_unstarted_member_bindings(self.home)

    @contextlib.contextmanager
    def work(self, background=False):
        """Admit new model work. Background workers get False and leave queues untouched."""
        depth = getattr(self._admitted, "depth", 0)
        if depth == 0:
            with self.admission:
                paused = self.update_gate is not None
                if not paused:
                    self.in_flight += 1
            if paused:
                if not background:
                    raise UpdatePaused(PAUSED_FOR_UPDATE)
                yield False
                return
        self._admitted.depth = depth + 1
        try:
            yield True
        finally:
            self._admitted.depth = depth
            if depth == 0:
                with self.admission:
                    self.in_flight -= 1
                    self.admission.notify_all()

    def paused(self):
        with self.admission:
            return self.update_gate

    def close_admission(self, gate, timeout):
        """Close the gate and wait for already admitted work to finish starting."""
        with self.admission:
            if self.update_gate is not None:
                raise board.Conflict("已有 Crew 更新正在准备")
            self.update_gate = gate
            if self.admission.wait_for(lambda: self.in_flight == 0, timeout):
                return True
            self.update_gate = None
            return False

    def open_admission(self, gate):
        with self.admission:
            if self.update_gate != gate:
                return False
            self.update_gate = None
            return True

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
        if resume:
            self._check_offline(session)
        with closing(store.connect(self.home)) as conn:
            options = models.get(conn, session_id)
            runtime = runtimes.get(conn, session_id)["runtime"]
            native_id=runtimes.get(conn,session_id)['native_id']
            pending=runtime_switch.pending(conn,session_id)
            permission_mode = settings.get_execution(conn)['claude']
            assistant = personal.is_assistant(conn, session_id)
            lost = self.missing_native(conn, session)
            handoff = None if lost is None else runtime_switch.handoff(
                conn, session, self.journal(session_id), intro='lost_native_handoff')['text']
            remembered = conn.execute('SELECT native_id FROM session_runtime_bindings WHERE session_id=? AND runtime=?',
                                      (session_id, runtime)).fetchone()
        # Inputs that never reached a native leave no history to resume.
        no_output = not has_native_output(self.journal(session_id).events)
        # A native this runtime once bound may hold accepted input without output yet.
        fresh_native = no_output and (remembered is None or remembered['native_id'] is None)
        if resume and native_id is None and not fresh_native and (pending is None or pending['to_runtime']!=runtime):
            raise board.BoardError('原生会话 ID 丢失，不能猜测恢复或偷偷新建')
        native_resume=resume and native_id is not None
        if assistant and runtime == 'claude' and native_id == session_id and no_output:
            # V2 0.3.1 prefilled a Claude identity before the assistant ever
            # started. Only that untouched assistant can take the first-start
            # path; any actual native output still requires resume.
            native_resume = False
        if lost is not None:
            # Claude has no conversation under this ID; start it new, announced as new.
            native_resume = False
        if runtime == "codex":
            from .codex_runtime import CodexRunning
            current = CodexRunning(self.home, self.plugin_root, session, spec, options,
                                   self.journal(session_id), self.send_message,
                                   process_factory=self.process_factory, resume=native_resume,
                                   goal_handler=self.goal_controller.agent_action, maintenance=maintenance,
                                   personal_handler=self.personal_action)
            self.processes[session_id] = current
            self.changes.session(session_id)
            return spawn.command(session_id, session["crew_name"], session["role"], runtime="codex", **options)
        exe = claude_executable()
        if native_id not in (None,session_id):
            raise board.BoardError('Claude 原生身份与 Crew 身份不一致，不能恢复错误会话')
        install.ensure(self.home, self.plugin_root)
        command = spawn.command(session_id, session["crew_name"], session["role"], native_resume, **options, agent=spec["agent"],
                                permission_mode=permission_mode, plugin_root=self.plugin_root,
                                preview_directory=report_preview.image_directory(self.home, session_id, create=True))
        if spec.get('_workspace_notice'):
            command += ['--append-system-prompt', spec['_workspace_notice']]
        with closing(store.connect(self.home)) as prompt_conn:
            prompt_manifest = behavior_prompts.for_session(prompt_conn, session_id, plugin_root=self.plugin_root)
        command = behavior_prompts.append_to_command(command, behavior_prompts.render(prompt_manifest))
        command[0] = exe
        journal = self.journal(session_id)
        journal.reset_runtime()
        identity = {**spec, "name": session["crew_name"], "role": session["role"], "group": session["group_name"]}
        environment = child_environment(identity)
        environment.update(CREW_SESSION_ID=session_id, CREW_HOME=str(self.home.resolve()))
        environment['PATH'] = str(self.home.resolve() / 'bin') + os.pathsep + environment.get('PATH', '')
        process = self.process_factory(command, cwd=session["cwd"], env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        current = Running(process, journal)
        current.resumed_native = session_id if native_resume else None
        current.replacing = None if lost is None else {'lost_native': lost}
        current.replacement_handoff = (None if lost is None else
            '【Crew 新会话交接：原会话 %s 已丢失，这是新开的会话，原会话里的上下文没有接回】\n%s\n【交接结束】' % (lost, handoff))
        current.behavior_prompt_hash = behavior_prompts.manifest_hash(prompt_manifest)
        with closing(store.connect(self.home)) as prompt_conn:
            behavior_prompts.record_applied(prompt_conn, session_id, prompt_manifest, status='sent',
                evidence='Claude 进程创建成功；启动参数已携带 Crew 配置，原生读取尚未回报')
        current.maintenance = maintenance
        self.processes[session_id] = current
        self.changes.session(session_id)
        self._control(current, "get_settings")
        self._control(current, "get_context_usage")
        current.stderr_reader = threading.Thread(target=self._stderr, args=(current,), name="crew-chat-stderr", daemon=True)
        current.stderr_reader.start()
        current.reader = threading.Thread(target=self._read, args=(session_id, current), name="crew-chat", daemon=True)
        current.reader.start()
        return command

    @_admitted
    def start(self, conn, project_name, name, task=None, group=None, role="member", members=(), cwd=None,
              new_worktree=None, base=None, now=None, member_session_ids=(), model=models.INHERIT, effort=models.INHERIT, agent=None,
              runtime=models.INHERIT, caller_session_id=None, caller_execution_id=None, goal_id=None, _registered=None,
              speed=models.INHERIT):
        with self.lock:
            bound_goal = None
            if caller_execution_id is not None and caller_session_id is None:
                raise board.BoardError('目标派生人员必须明确调用身份')
            group = spawn.authorize(conn, project_name, caller_session_id, role, group)
            if caller_session_id not in (None, board.HUMAN):
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
            if role == 'member' and caller_session_id not in (None, board.HUMAN) and bound_goal is None:
                # Members are executions of one part of a task, not standing teammates.
                raise board.BoardError('执行只能开在任务里：crew spawn --goal <任务ID> --name <这块活的短名> --task "<任务书>"'
                                       '（Codex 用 crew_spawn 的 goal_id）；没有任务的零活自己做，或用原生子代理、一次性派工')
            if (role == 'member' and bound_goal is not None and caller_execution_id is None
                    and caller_session_id not in (None, board.HUMAN) and conn.execute(
                        "SELECT 1 FROM goal_executions e JOIN goals g ON g.id=e.goal_id WHERE e.goal_id=? AND e.session_id=? "
                        "AND e.round=g.current_round AND e.status NOT IN ('completed','failed','cancelled')",
                        (bound_goal, caller_session_id)).fetchone()):
                # The task's own execution splits it; a second splitter in the general chat duplicates parts.
                raise board.Conflict('这个任务已有你自己的任务执行在推进，拆分和开执行由那段执行负责，总对话不再重复开；'
                                     '要补充要求请发给该任务（goal_id）')
            if role == "leader":
                plan = spawn.leader_plan(conn, self.home, self.plugin_root, project_name, name, members, task,
                                         cwd=cwd, new_worktree=new_worktree, base=base, now=now,
                                         member_session_ids=member_session_ids, model=model, effort=effort, group=group, agent=agent, runtime=runtime,
                                         speed=speed)
            else:
                plan = spawn.prepare(conn, self.home, self.plugin_root, project_name, name, task, group, role,
                                     cwd=cwd, new_worktree=new_worktree, base=base, now=now, model=model, effort=effort, runtime=runtime,
                                     speed=speed)
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
                # Only the general conversation launched below creates a Claude
                # native under this ID. A goal-bound member works in its own
                # execution native instead; its general conversation stays
                # unbound until a native actually reports it.
                runtimes.set(conn, session_id, chosen_runtime,
                             session_id if chosen_runtime == "claude" and bound_goal is None else None, now=now)
                models.set(conn, session_id, spec["model"], spec["effort"], now=now, speed=spec.get("speed"))
                if _registered is not None:
                    _registered(session_id)
                try:
                    with self.lock:
                        if bound_goal is not None:
                            goals.assign(conn, bound_goal, session_id, task, actor=caller_session_id)
                            goal_receipt.update(self.goal_controller.send(bound_goal, session_id, task, source='crew'))
                            return
                        plan["command"] = self._launch(session, spec)
                        # Claude used to fetch this brief with Read before seeing the task.
                        text = plan["files"][spec["md"]] if chosen_runtime == "claude" else spec["task"]
                        self._send(session_id, text, spec["task"], source="crew")
                except Exception:
                    live = [key for key in self._identity_keys(conn, session_id) if self.running(key)]
                    status = ('working' if any(self.busy(key) for key in live) else 'idle') if live else 'offline'
                    board.set_status(conn, session_id, status, now=now)
                    raise

            result = spawn._execute(conn, plan, launch, members, member_session_ids)
            return {**result, **goal_receipt, "session_id": session_id, "role": spec["role"]}
