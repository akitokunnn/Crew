from contextlib import closing

from .. import board, session_ops, store
from .. import codex_client
from .. import session_delete, session_presence


class SessionsMixin:
    def archive_session(self, conn, session_id, removed=False, now=None, *, caller_session_id=None):
        with self.chat.lock:
            session_ops.authorize_archive(conn, session_id, caller_session_id)
            if caller_session_id not in (None, board.HUMAN):
                busy = any(self.chat.busy(key) for key in self.chat._identity_keys(conn, session_id))
                bindings = conn.execute('SELECT b.* FROM goal_execution_bindings b JOIN goal_executions e ON e.id=b.execution_id '
                                        'WHERE e.session_id=?', (session_id,))
                if busy or any(self.chat.scope_busy(row['execution_id'], row['runtime'], row['native_id']) for row in bindings):
                    raise board.Conflict('成员正在执行，请先等执行结束再归档')
            result = session_ops.archive(conn, session_id, removed=removed, now=now, caller_session_id=caller_session_id)
            self.chat.goal_controller.sync(conn)
            self.chat.stop_managed(session_id)
            return result

    def delete_session(self, conn, session_id, now=None):
        """Tombstone the person (a leader with its whole group), stop owned processes,
        then delete its web journal and native CC / Codex threads."""
        with self.chat.lock:
            _, people = session_ops.deletion_scope(conn, session_id)
            ids = [person['session_id'] for person in people]
            natives = {sid: session_delete.targets(conn, sid) for sid in ids}
            self._assert_deletable(conn, people, natives)
            result = session_ops.delete(conn, session_id, now=now)
            self.chat.goal_controller.sync(conn)
            for sid in ids:
                self.chat.stop_managed(sid)
                self.chat.journals.pop(sid, None)
        errors, codex = [], {}
        for sid in ids:
            try:
                self.chat.path(sid).unlink(missing_ok=True)
            except OSError as exc:
                errors.append('网页对话记录删除失败：%s' % exc)
            for native in natives[sid]:
                if native['runtime'] == 'codex':
                    codex.setdefault(native['home'], []).append(native['native_id'])
                    continue
                try:
                    session_delete.delete_claude(self.claude_root, native['native_id'])
                except (OSError, ValueError) as exc:
                    errors.append('Claude Code 会话 %s 删除失败：%s' % (native['native_id'], exc))
        for home, native_ids in codex.items():
            try:
                session_delete.delete_codex(native_ids, home, process_factory=self.chat.process_factory)
            except (codex_client.CodexError, OSError, ValueError) as exc:
                errors.append('Codex 会话 %s 删除失败：%s' % ('、'.join(native_ids), exc))
        return {**result, 'errors': errors}

    def _assert_deletable(self, conn, people, natives):
        """Never delete a transcript that a process outside Crew is still writing."""
        with self.agents_lock:
            agents, agents_error = self.agents, self.agents_error
        external = {agent['sessionId'] for agent in session_presence.external_agents(self.chat, agents)}
        for person in people:
            sid, name = person['session_id'], person['crew_name'] or person['session_id']
            claude = [native['native_id'] for native in natives[sid] if native['runtime'] == 'claude']
            if claude and agents_error is not None:
                raise board.Conflict('暂时读不到 Claude Code 进程状态，无法确认「%s」是否还开着，请稍后再删' % name)
            if external.intersection(claude):
                raise board.Conflict('「%s」还开在 VS Code 或终端里，先关掉那边的会话再删除' % name)

    def stop_archived(self, before, with_members=True):
        with self.chat.lock, closing(store.connect(self.home)) as conn:
            self.chat.goal_controller.sync(conn)
            for entry in before['sessions']:
                if with_members or entry['session_id'] == before['group']['leader_session_id']:
                    self.chat.stop_managed(entry['session_id'])
