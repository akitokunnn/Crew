"""List and resolve carrier approvals through the shared execution registry."""
from dataclasses import dataclass

from . import board, goals, messaging


@dataclass
class Approvals:
    executions: object

    def listing(self, project):
        with self.executions.locked():
            return sorted([approval for current in self.executions.processes.values()
                           if getattr(current, 'runtime', 'claude') == 'codex'
                           for approval in current.pending_approvals() if approval['project'] == project],
                          key=lambda approval: (approval['created_at'], approval['id']))

    def resolve(self, conn, project, identity, allow):
        if type(allow) is not bool:
            raise ValueError('allow 必须是 true 或 false')
        with self.executions.locked():
            for key, current in self.executions.processes.items():
                if getattr(current, 'runtime', 'claude') != 'codex':
                    continue
                approval = next((item for item in current.pending_approvals()
                                 if item['id'] == identity and item['project'] == project), None)
                if approval is None:
                    continue
                if not self.executions.running(key) or messaging.stopped(conn, approval['session_id']):
                    raise board.Conflict('该原生执行已停止，审批已经失效')
                if approval['execution_id'] is not None:
                    gate = goals.dispatch_check(conn, approval['goal_id'], execution_id=approval['execution_id'])
                    if not gate['allowed']:
                        raise board.Conflict(gate['reason'])
                return {'approval': current.resolve_approval(identity, allow)}
        raise board.Conflict('审批已结束或不属于当前工作区，请刷新')
