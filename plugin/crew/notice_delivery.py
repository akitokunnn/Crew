"""Deliver persisted collaboration notices and Git receipts through session ports.

The caller owns the polling lifetime, admission gate and database writer scope.
This module owns batch identity, receipt handling and the existing eligibility
rules, and knows nothing about HTTP or web.Service.
"""
from dataclasses import dataclass
import hashlib

from . import board, codex_client, messaging, runtimes


@dataclass
class Delivery:
    executions: object
    send: object
    wake: object
    observe_agents: object
    clock: object

    def deliver(self, conn):
        self.git_results(conn)
        leaders = [dict(row) for row in conn.execute(
            "SELECT DISTINCT s.* FROM sessions s JOIN leader_notices n ON n.session_id=s.session_id "
            "WHERE n.sent_at IS NULL AND s.role='leader' AND s.status!='closed'")]
        for leader in leaders:
            sid = leader['session_id']
            batch = None
            try:
                with self.executions.locked():
                    key = sid
                    if messaging.stopped(conn, sid) or self.executions.maintenance_held(sid):
                        continue
                    if self.executions.running(key):
                        if self.executions.busy(key):
                            continue
                    else:
                        observed = self.observe_agents()
                        agents, agents_error = observed.agents, observed.error
                        # Unknown process state could mean the leader runs elsewhere; resuming then would start a second copy.
                        claude = runtimes.get(conn, sid)['runtime'] == 'claude'
                        if claude and (agents_error is not None or not observed.ready):
                            continue
                        if claude and any(agent['sessionId'] == sid for agent in agents):
                            if conn.execute("SELECT 1 FROM events WHERE session_id=? AND kind='leader:notice_blocked'",
                                            (sid,)).fetchone() is None:
                                board.record_event(conn, 'leader:notice_blocked', leader['project'], sid,
                                                   {'reason': '会话正在其他进程中运行'}, now=self.clock().isoformat())
                            continue
                        if not self.executions.path(key).exists():
                            self.executions.journal(key).set_status('idle')
                    pending = board.pending_notices(conn, sid)
                    if not pending:
                        continue
                    pending_ids = {notice['id'] for notice in pending}
                    journal = self.executions.journal(sid)
                    batch = next((event for event in reversed(journal.events) if event['type'] == 'leader_notice_batch'
                                  and pending_ids.intersection(event['notice_ids'])), None)
                    if batch is None:
                        identity = 'leader-notices:' + hashlib.sha256(','.join(str(n['id']) for n in pending).encode()).hexdigest()
                        text = '[Crew]\n' + '\n'.join('%s. %s' % (index, notice['text'])
                                                       for index, notice in enumerate(pending, 1))
                        batch = journal.emit('leader_notice_batch', id=identity, text=text, notice_ids=[n['id'] for n in pending])
                    receipt = self.send(leader, batch['text'], source='crew', identity=batch['id'])
                    if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
                        raise board.Conflict('NativeReceiptUnknown: 通知输入尚无确认的接收回执')
                    for identity in pending_ids.intersection(batch['notice_ids']):
                        board.mark_notice_sent(conn, identity, now=self.clock().isoformat())
            except (board.BoardError, codex_client.CodexError, OSError) as exc:
                detail = '%s: %s' % (type(exc).__name__, exc)
                if batch is not None:
                    text = '协作通知投递未确认，已保留原消息，不重复执行。' + detail
                    if any(event['type'] == 'error' and event.get('notice_batch') == batch['id']
                           and event.get('text') == text for event in journal.events):
                        continue
                    journal.emit('error', notice_batch=batch['id'], text=text)
                board.record_event(conn, 'leader:notice_error', leader['project'], sid,
                                   {'error': detail}, now=self.clock().isoformat())


    def git_results(self, conn):
        pending = conn.execute(
            "SELECT q.*,h.body FROM git_result_handoffs q JOIN handoffs h ON h.id=q.handoff_id "
            "JOIN git_merge_requests r ON r.id=q.request_id AND r.project=h.project AND r.session_id=q.session_id "
            "WHERE (r.status='rejected' OR (r.status='merged' AND r.blocking=1)) AND NOT EXISTS(SELECT 1 FROM handoff_deliveries d "
            "WHERE d.handoff_id=q.handoff_id AND d.session_id=q.session_id) ORDER BY q.handoff_id").fetchall()
        for result in pending:
            sid = result['session_id']
            target = board.session(conn, sid)
            if target['status'] == 'closed' or board.is_archived(conn, sid):
                continue
            try:
                with self.executions.locked():
                    key = sid
                    if messaging.stopped(conn, sid):
                        continue
                    observed = self.observe_agents()
                    claude = runtimes.get(conn, sid)['runtime'] == 'claude'
                    if claude and not self.executions.running(key) and (observed.error is not None or not observed.ready):
                        continue
                    external = claude and any(agent['sessionId'] == sid for agent in observed.agents)
                    if self.executions.running(key):
                        if self.executions.busy(key):
                            continue
                        self.send(target, '[Crew]\n' + result['body'], source='crew')
                    else:
                        # Unadopted requesters cannot be woken; their next prompt picks the result up as a handoff.
                        if external or target['crew_name'] is None:
                            continue
                        self.wake(conn, target, result['body'],
                                       caller_session_id=result['admin_session_id'], now=self.clock().isoformat())
                    conn.execute("INSERT OR IGNORE INTO handoff_deliveries VALUES(?,?,?)",
                                 (result['handoff_id'], sid, self.clock().isoformat()))
            except (board.BoardError, OSError) as exc:
                board.record_event(conn, 'git:result_error', target['project'], sid,
                                   {'handoff_id': result['handoff_id'], 'error': '%s: %s' % (type(exc).__name__, exc)},
                                   now=self.clock().isoformat())

