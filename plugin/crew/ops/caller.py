"""Who is calling an operation (design §3.1)."""
from dataclasses import dataclass

from .registry import AI_ROLES


@dataclass(frozen=True)
class Caller:
    role: str
    session_id: str = None
    execution_id: str = None
    goal_id: str = None
    project: str = None
    runtime: str = None
    device_id: str = None
    token_hash: str = None

    @property
    def is_ai(self):
        return self.role in AI_ROLES

    @property
    def key(self):
        """Stable identity for request receipts; survives token rotation (§5.5)."""
        if self.role == 'human':
            return 'human'
        if self.role.startswith('device_'):
            return 'device:%s' % self.device_id
        if self.execution_id:
            return 'execution:%s' % self.execution_id
        return 'session:%s' % self.session_id


HUMAN = Caller('human')


def device(identity):
    kind = identity.get('kind')
    return Caller('device_desktop' if kind == 'desktop' else 'device_browser',
                  device_id=str(identity.get('id') or identity.get('device_id') or kind))
