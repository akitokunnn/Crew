import base64
from contextlib import closing
import hashlib
import json
import uuid

from . import behavior_prompts, trust_mode
from . import (
    board, channel, codex_client, git_admin, guard, messaging, personal, runtime_switch, runtimes, store,
    workspace_execution,
)
from .chat_base import MAINTENANCE_HELD, _admitted, IMAGE_TYPES, validate_images


# A leader notice moves one way: queued -> claimed (by the web service's input batch, or by an external
# session's prompt hook) -> sent. Whoever's guarded update claims it first delivers it; the other leaves it.
# Every state below is read from stored facts only; "replied" comes from the leader's answering post.
WAITING = {'external': '对方在外部窗口运行，下次提示时送达', 'busy': '对方正在忙，这一轮结束后送达',
           'stopped': '用户已停止对方，用户再次发话前不会送达'}


def notice_state(row):
    """The delivery of one channel mention or reply notice, from its notice row and the answering post."""
    if row['notice_id'] is None:
        return {'delivery': 'none', 'queued_at': None, 'delivered_at': None, 'via': None, 'waiting': None,
                'reply_id': row['reply_id']}
    sent = row['sent_at'] is not None
    return {'delivery': 'replied' if row['reply_id'] is not None else 'delivered' if sent else 'queued',
            'queued_at': row['queued_at'], 'delivered_at': row['sent_at'],
            'via': (row['delivered_via'] or 'input') if sent else None,
            'waiting': None if sent else row['waiting'], 'reply_id': row['reply_id']}


def claim_batch(conn, notice_ids, batch_id):
    """The web service's input batch takes these notices before it is written; ones the hook took stay out."""
    return [notice_id for notice_id in notice_ids if conn.execute(
        'UPDATE leader_notices SET batch_id=? WHERE id=? AND sent_at IS NULL AND (batch_id IS NULL OR batch_id=?)',
        (batch_id, notice_id, batch_id)).rowcount]


def claim_for_hook(conn, session_id, now=None):
    """An external session's prompt hook takes this leader's queued channel notices no batch owns."""
    pending = board._rows(conn, 'SELECT n.id,n.text FROM channel_mentions t JOIN leader_notices n ON n.id=t.notice_id '
                                'WHERE t.session_id=? AND n.sent_at IS NULL AND n.batch_id IS NULL ORDER BY n.id',
                          (session_id,))
    return [notice['text'] for notice in pending if conn.execute(
        "UPDATE leader_notices SET sent_at=?,delivered_via='hook' WHERE id=? AND sent_at IS NULL AND batch_id IS NULL",
        (board._time(now), notice['id'])).rowcount]


def note_waiting(conn, session_id, reason):
    """Why this leader's queued notices still wait; written only when the reason changes."""
    conn.execute('UPDATE leader_notices SET waiting=? WHERE session_id=? AND sent_at IS NULL AND waiting IS NOT ?',
                 (reason, session_id, reason))


def _first_turn_succeeded(journal, identity):
    """Whether the first turn to end after this input was accepted ended well: failed, stopped or lost is no."""
    accepted = False
    for event in journal.events:
        if event['type'] == 'input_delivery' and event['message_id'] == identity and event['status'] == 'accepted':
            accepted = True
        elif accepted and event['type'] == 'turn_result':
            return event.get('success') is True
    return False


class DeliveryMixin:
    def _send(self, session_id, text, display_text=None, source='human', identity=None, images=None, sender=None,
              steer=False):
        current = self.processes[session_id]
        logical = current.execution['session_id'] if current.execution is not None else session_id
        if self.maintenance_held(logical):
            raise board.Conflict(MAINTENANCE_HELD)
        identity = identity or str(uuid.uuid4())
        original_text=text if display_text is None else display_text
        prompt_application = None
        if text.strip() != '/compact':
            with closing(store.connect(self.home)) as prompt_conn:
                current_manifest = behavior_prompts.for_session(prompt_conn, logical, plugin_root=self.plugin_root)
                if behavior_prompts.manifest_hash(current_manifest) != getattr(current, 'behavior_prompt_hash', None):
                    prompt_application = behavior_prompts.prepare_input(prompt_conn, logical, session_id, identity,
                                                                        plugin_root=self.plugin_root)
            if prompt_application is not None:
                text = behavior_prompts.render(prompt_application['manifest']) + '\n\n' + text
        handoff_id=None
        channel_upto = None
        git_revision = getattr(current, 'git_context_revision', None)
        trust_context = getattr(current, 'trust_context', None)
        if text.strip() != '/compact':
            with closing(store.connect(self.home)) as handoff_conn:
                assistant = personal.is_assistant(handoff_conn, logical)
                git_revision, git_context = ((None, None) if assistant else
                    git_admin.input_context(handoff_conn, logical, git_revision, self.plugin_root))
                if current.execution is None:
                    text,handoff_id=runtime_switch.input_text(handoff_conn,session_id,text,identity)
                    if current.runtime == 'claude' and current.replacement_handoff is not None:
                        text = current.replacement_handoff + '\n\n' + text
                        current.replacement_handoff = None
                    trust_context, authorization = trust_mode.input_context(handoff_conn, session_id, trust_context)
                    if authorization:
                        text = authorization + '\n\n' + text
                    summary = self.goal_controller.general_context(handoff_conn, session_id)
                    if summary:
                        text = summary + '\n\n' + text
                    if getattr(current, 'runtime', 'claude') == 'codex' and not assistant:
                        # Claude leaders get this through the prompt hook; Codex has no hook. The cursor
                        # moves only once this input is accepted, so a rejected turn sees the posts again.
                        posts, channel_upto = channel.preview(handoff_conn, logical)
                        if posts:
                            text = posts + '\n\n' + text
                    if assistant and source in ('human', 'qq', 'lark'):
                        text = ('【Crew 本轮原始用户消息标识：%s。写入动作必须绑定此 message_id；'
                                '不改变用户原话的授权范围。】\n\n' % identity) + text
            if git_context:
                text = git_context + '\n\n' + text
        files = []
        content = text
        if images:
            directory = self.home / "chat" / "files" / session_id
            directory.mkdir(parents=True, exist_ok=True)
            for index, image in enumerate(images, 1):
                name = identity + "-" + str(index) + IMAGE_TYPES[image["media_type"]]
                (directory / name).write_bytes(base64.b64decode(image["data"], validate=True))
                files.append(name)
            content = [{"type": "image", "source": {"type": "base64", "media_type": image["media_type"],
                                                     "data": image["data"]}} for image in images]
            content.append({"type": "text", "text": text})
        if getattr(current, "runtime", "claude") == "codex":
            try:
                current.interrupt_requested = False
                result=current.send(text, identity, display_text=original_text, source=source,
                                    images=images, sender=sender, image_files=files, steer=steer)
                if prompt_application is not None:
                    with closing(store.connect(self.home)) as prompt_conn:
                        behavior_prompts.delivered(prompt_conn, prompt_application['id'], confirmed=True,
                            evidence='Codex 原生 turn/start 或 turn/steer 已接收输入；thread=' + str(current.thread_id))
                    current.behavior_prompt_hash = behavior_prompts.manifest_hash(prompt_application['manifest'])
                current.git_context_revision = git_revision
                current.trust_context = trust_context
                if handoff_id is not None:
                    with closing(store.connect(self.home)) as handoff_conn:
                        runtime_switch.accepted(handoff_conn,session_id,identity,handoff_id)
                if channel_upto is not None:
                    with closing(store.connect(self.home)) as channel_conn:
                        channel.seen(channel_conn, logical, channel_upto)
                return result
            except Exception as exc:
                with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
                raise
        message = {"type": "user", "uuid": identity,
                   "session_id": current.native_id if current.execution else session_id, "parent_tool_use_id": None,
                   "message": {"role": "user", "content": content}}
        try:
            with current.input_lock:
                if current.execution is None and current.journal.status != 'working':
                    with closing(store.connect(self.home)) as conn:
                        guard.clear_general_handoff(conn, session_id)
                current.interrupt_requested = False
                current.journal.message(identity, original_text, source=source, images=files, sender=sender)
                current.pending_native_accept=(identity,handoff_id) if handoff_id is not None else None
                current.journal.set_status("working")
                current.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                current.process.stdin.flush()
                if prompt_application is not None:
                    with closing(store.connect(self.home)) as prompt_conn:
                        behavior_prompts.delivered(prompt_conn, prompt_application['id'],
                            evidence='Claude 输入已写入并flush；未将传输成功冒充模型读取')
                    current.behavior_prompt_hash = behavior_prompts.manifest_hash(prompt_application['manifest'])
                current.git_context_revision = git_revision
                current.trust_context = trust_context
        except Exception as exc:
            with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
            raise
        return identity

    def prepare_runtime(self,conn,session_id,runtime,catalog,model=None,effort=None,handoff=None,now=None,speed=None):
        with self.lock:
            return runtime_switch.prepare(conn,board.session(conn,session_id),self,runtime,catalog,model,effort,handoff,now,speed=speed)

    def switch_runtime(self,conn,session_id,preview_id,confirm,catalog,now=None):
        with self.lock:
            return runtime_switch.switch(conn,board.session(conn,session_id),self,preview_id,confirm,catalog,now)

    def send_message(self, sender_id, target_id, text, message_id=None, execution_id=None, kind='information', reply_to=None):
        with self.lock, closing(store.connect(self.home)) as conn:
            from . import goal_messages
            if (execution_id or reply_to is not None
                    or goal_messages.leader_task(conn, sender_id, board.session(conn, target_id)) is not None):
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
            goal_messages.remind_unfinished(self.goal_controller, conn)
            for message in messaging.pending(conn):
                sid = message["target_session_id"]
                target = board.session(conn, sid)
                if target["status"] == "closed" or board.is_archived(conn, sid):
                    conn.execute("UPDATE peer_messages SET error=? WHERE id=?", ("目标已归档或移除", message["id"]))
                    continue
                if messaging.stopped(conn, sid):
                    continue
                # A leader's word reaches its own member mid-turn, like the user's; other mail waits.
                steer = goal_messages.from_leader(conn, message["sender_session_id"], target)
                if self.busy(sid) and not steer:
                    continue
                sender = board.session(conn, message["sender_session_id"])
                body = messaging.text(conn, message)
                try:
                    if not self.path(sid).exists():
                        self.journal(sid).set_status('idle')
                    result = self.send(target, body, display_text=message['body'], source='peer',
                                       identity='peer:' + message['id'], sender=sender['crew_name'], steer=steer)
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

    def deliver_leader_notices(self, conn, live, now):
        """Write each idle leader's queued notices into its input as one batch.

        ``live`` holds the Claude sessions the agents list shows running in another process, or is None
        while that list is unknown; such a leader is never resumed here, its prompt hook delivers instead.
        """
        leaders = [dict(row) for row in conn.execute(
            "SELECT DISTINCT s.* FROM sessions s JOIN leader_notices n ON n.session_id=s.session_id "
            "WHERE n.sent_at IS NULL AND n.wake=1 AND s.role='leader' AND s.status!='closed'")]
        for leader in leaders:
            sid = leader['session_id']
            batch = journal = None
            try:
                with self.lock:
                    git_admin.wake_for_new_requests(conn, sid, now=now)
                    if messaging.stopped(conn, sid):
                        note_waiting(conn, sid, 'stopped')
                        continue
                    if self.maintenance_held(sid):
                        continue
                    if self.running(sid):
                        if self.busy(sid):
                            note_waiting(conn, sid, 'busy')
                            continue
                    else:
                        claude = runtimes.get(conn, sid)['runtime'] == 'claude'
                        if claude and live is None:
                            continue
                        if claude and sid in live:
                            note_waiting(conn, sid, 'external')
                            if conn.execute("SELECT 1 FROM events WHERE session_id=? AND kind='leader:notice_blocked'",
                                            (sid,)).fetchone() is None:
                                board.record_event(conn, 'leader:notice_blocked', leader['project'], sid,
                                                   {'reason': '会话正在其他进程中运行'}, now=now)
                            continue
                        if not self.path(sid).exists():
                            self.journal(sid).set_status('idle')
                    note_waiting(conn, sid, None)
                    journal = self.journal(sid)
                    batch = self._notice_batch(conn, journal, board.pending_notices(conn, sid))
                    if batch is None:
                        continue
                    receipt = self.send(leader, batch['text'], source='crew', identity=batch['id'],
                                        display_text=batch.get('display'), sender=batch.get('sender'))
                    if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
                        raise board.Conflict('NativeReceiptUnknown: 通知输入尚无确认的接收回执')
                    for notice_id in batch['notice_ids']:
                        board.mark_notice_sent(conn, notice_id, now=now)
            except (board.BoardError, codex_client.CodexError, OSError) as exc:
                detail = '%s: %s' % (type(exc).__name__, exc)
                if batch is not None:
                    text = '协作通知投递未确认，已保留原消息，不重复执行。' + detail
                    if any(event['type'] == 'error' and event.get('notice_batch') == batch['id']
                           and event.get('text') == text for event in journal.events):
                        continue
                    journal.emit('error', notice_batch=batch['id'], text=text)
                board.record_event(conn, 'leader:notice_error', leader['project'], sid, {'error': detail}, now=now)

    def _notice_batch(self, conn, journal, pending):
        """The batch to write now: the one already claimed, or a new claim recorded before the journal entry."""
        owned = next((notice['batch_id'] for notice in pending if notice['batch_id'] is not None), None)
        if owned is not None:
            ids = [notice['id'] for notice in pending if notice['batch_id'] == owned]
            existing = next((event for event in reversed(journal.events)
                             if event['type'] == 'leader_notice_batch' and event['id'] == owned), None)
            if existing is not None:
                return {**existing, 'notice_ids': ids}
            pending = [notice for notice in pending if notice['id'] in ids]
        else:
            # The user's own channel words go in a batch of their own, shown in private as the user's message.
            from_user = channel.user_mentions(conn, [notice['id'] for notice in pending])
            if from_user:
                pending = [notice for notice in pending if notice['id'] in from_user]
            if not pending:
                return None
            digest = hashlib.sha256(','.join(str(n['id']) for n in pending).encode()).hexdigest()
            # The id carries the user's first post so the private bubble can link back to the channel.
            owned = ('channel:%d:%s' % (from_user[pending[0]['id']]['id'], digest[:32]) if from_user
                     else 'leader-notices:' + digest)
            claimed = set(claim_batch(conn, [notice['id'] for notice in pending], owned))
            pending = [notice for notice in pending if notice['id'] in claimed]
            if not pending:
                return None
        from_user = channel.user_mentions(conn, [notice['id'] for notice in pending])
        shown = ({'sender': board.HUMAN, 'display': '\n\n'.join(from_user[notice['id']]['body'] for notice in pending)}
                 if from_user else {})
        text = '[Crew]\n' + '\n'.join('%s. %s' % (index, notice['text']) for index, notice in enumerate(pending, 1))
        return journal.emit('leader_notice_batch', id=owned, **shown, text=text,
                            notice_ids=[notice['id'] for notice in pending])

    def settle_channel_mentions(self, conn, now):
        """A user mention the leader answered only in private gets a channel pointer once that turn ended well."""
        for row in conn.execute("SELECT DISTINCT t.session_id FROM channel_mentions t JOIN leader_notices n "
                                "ON n.id=t.notice_id WHERE t.await_reply=1 AND n.sent_at IS NOT NULL "
                                "AND n.delivered_via IS NULL").fetchall():
            sid = row['session_id']
            with self.lock:
                if self.busy(sid) or not self.path(sid).exists():
                    continue
                journal = self.journal(sid)
            answered = {item['message_id'] for item in channel.awaiting(conn, sid)
                        if item['batch_id'] is not None and _first_turn_succeeded(journal, item['batch_id'])}
            if not answered:
                continue
            try:
                channel.settle(conn, sid, answered, now=now)
            except board.BoardError as exc:
                board.record_event(conn, 'channel:settle_error', None, sid,
                                   {'error': '%s: %s' % (type(exc).__name__, exc)}, now=now)

    @_admitted
    def send(self, session, text, source='human', identity=None, images=None, display_text=None, sender=None,
             *, resume_existing=False, steer=False):
        """``steer`` lets a non-human input join a turn already in progress, as the user's input does."""
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
                if '_execution' not in session:
                    if messaging.stopped(routing_conn, session['session_id']):
                        if source not in ('human', 'qq', 'lark'):
                            raise board.Conflict('用户已停止此会话；排队消息不能自动恢复它')
                        messaging.clear_stop(routing_conn, session['session_id'])
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
                            'delivery': prior['delivery'], 'duplicate': True}
                if prior['status'] != 'rejected':
                    raise board.Conflict("上次输入的原生接收状态未确认，请先核对会话，避免重复执行")
            elif identity in journal.messages:
                raise board.Conflict("这条历史输入没有可确认的接收回执，请先核对会话")
            if (not resume_existing and '_execution' not in session and session['role'] == 'member'
                    and source not in ('human', 'qq', 'lark')):
                with closing(store.connect(self.home)) as check_conn:
                    active = next((row['id'] for row in check_conn.execute(
                        'SELECT e.id,e.runtime,b.native_id FROM goal_executions e LEFT JOIN goal_execution_bindings b '
                        'ON b.execution_id=e.id AND b.runtime=e.runtime WHERE e.session_id=?', (session_id,))
                        if self.scope_busy(row['id'], row['runtime'], row['native_id'])), None)
                if active is not None:
                    raise board.Conflict('成员的目标执行正在工作，不能向总对话重复派工；'
                                         '请明确目标继续原执行或等待当前任务结束：' + active)
            steering = self.running(session_id) and self.journal(session_id).status == "working"
            if steering and source not in ("human", "qq", "lark") and not steer:
                raise board.Conflict("会话正在回复，自动消息等待当前轮次结束")
            if not self.running(session_id):
                with closing(store.connect(self.home)) as spec_conn:
                    spec, _ = self._resume_spec(spec_conn, session)
                self._launch(session, spec, resume=True)
            delivery = 'steered' if steering else 'started'
            quiet = []
            if source in ('human', 'qq', 'lark') and '_execution' not in session and session['role'] == 'leader':
                # Results the user left without waking the leader ride along with their next message.
                with closing(store.connect(self.home)) as notice_conn:
                    quiet = board.quiet_notices(notice_conn, session['session_id'])
                if quiet:
                    display_text = text if display_text is None else display_text
                    text = ('[Crew 留档通知：用户在你上次回复后处理了以下事项，供参考，按需继续安排]\n'
                            + '\n'.join('%s. %s' % (index, notice['text']) for index, notice in enumerate(quiet, 1))
                            + '\n\n[用户消息]\n' + text)
            journal.emit('input_delivery', message_id=identity, digest=digest, status='submitted', delivery=delivery)
            try:
                identity = self._send(session_id, text, display_text=display_text,
                                      source=source, identity=identity, images=images, sender=sender, steer=steer)
            except (codex_client.RpcError, board.BoardError) as exc:
                journal.emit('input_delivery', message_id=identity, digest=digest, status='rejected',
                             delivery=delivery, error=type(exc).__name__, detail=str(exc))
                raise
            except (codex_client.TransportError, codex_client.RequestTimeout, OSError) as exc:
                journal.emit('input_delivery', message_id=identity, digest=digest, status='unknown',
                             delivery=delivery, error=type(exc).__name__, detail=str(exc))
                raise
            journal.emit('input_delivery', message_id=identity, digest=digest, status='accepted', delivery=delivery)
            if quiet:
                with closing(store.connect(self.home)) as notice_conn:
                    for notice in quiet:
                        board.mark_notice_sent(notice_conn, notice['id'])
        return {"ok": True, 'message_id': identity, 'accepted': True,
                'delivery': delivery}
