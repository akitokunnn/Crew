"""Desktop actions that write shared data without the backend (design §10 item 9).

The desktop's operations worker (``desktop/src/operations.cjs``) runs some
actions directly so they work while the backend is down. Each that touches
shared Crew data is the same operation as its ``/api/op`` form.
"""
from .. import registry

WORKER = {'settings.get': 'settings-get', 'settings.save': 'settings-save', 'ops_chat.get': 'snapshot',
          'ops_chat.send': 'send', 'ops_chat.interrupt': 'interrupt', 'ops_config.get': 'config-get',
          'ops_config.save': 'config-save'}

for name, action in WORKER.items():
    registry.amend(name, ipc=('worker:' + action,))
