import json
import subprocess
import uuid

from . import behavior_prompts
from . import board, messaging, models, runtimes, store, workspace_execution
from .chat_base import REPLY_KIND, _admitted


class ResumeMixin:
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
                raise board.BoardError("目标已删除，不能叫醒")
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
                    reply = ('回复用 crew send ' + caller['crew_name'] + ' "<回复>" ' + REPLY_KIND + '；Codex 用 crew_send_message 并同样指定 kind。' if peer else
                             "回复用 SendMessage，地址「" + caller["cc_name"] + "」。" if caller["cc_name"] else
                             "回复前用 crew who " + caller["crew_name"] + " 查地址；离线时用 crew wake 接续原会话。")
                    notice = ("组员「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text + "。" + reply)
                else:
                    peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (caller, target))
                    if not peer and caller["cc_name"] is None:
                        raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                    notice = ("leader「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text +
                              ('。回复用 crew send ' + caller['crew_name'] + ' "<回复>" ' + REPLY_KIND + '，或 crew say --to ' + caller['crew_name'] + '。' if peer else
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
            if runtimes.get(conn, target["session_id"])["native_id"] is None:
                # A goal-bound member has worked only inside task executions.
                situation = "（这是你的总对话第一次启动；你在任务里的执行记录留在对应任务中，需要时用 crew goal status 查阅）"
            elif self.missing_native(conn, target) is not None:
                situation = "（你原来的原生会话已丢失，这是 Crew 新开的会话，任务背景交接在前面）"
            else:
                situation = "（你原来的会话进程已经结束，Crew 在后台接着运行你）"
            message = "[Crew] " + introduction + situation + "：\n\n" + text
            reply_to = leader if human else caller
            if reply_to is not None:
                peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (reply_to, target))
                if not peer and reply_to["cc_name"] is None:
                    raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                message += ('\n\n回复 leader 用 crew send ' + reply_to['crew_name'] + ' "<回复>" ' + REPLY_KIND + '；Codex 用 crew_send_message 并同样指定 kind。' if peer else
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
            self.runtime_recovery.recover(conn, agents, now, runtime=runtime)
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
                        self._resume(conn, session, behavior_prompts.text(conn, 'resume_nudge', session_id=session['session_id'], values={'reason': 'Crew 服务刚重启，你上一轮做到一半被打断了'}), now=now)
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
