"""The global assistant can do what the user can do in Crew's pages (user ruling 7).

Every operation open to the local user is opened to the assistant, acting as
the user: it runs the same handler with the user's argument shape and names the
workspace in ``project``. Writes need the original user message (``confirm``);
``high`` ones wait for the user's confirmation. Host administration, opening
programs on the host, and deciding AI approvals stay with the user.
"""
import copy

from .. import registry

EXCLUDED_PREFIXES = ('admin.', 'ops_chat.', 'ops_config.', 'connections.', 'remote_access.', 'storage.', 'data.',
                     'sounds.', 'interaction.', 'assistant.', 'unassigned.')
EXCLUDED = frozenset(('workspace.add_existing', 'workspace.create_folder', 'workspace.host_folders', 'files.open',
                      'files.reveal', 'goal.report.folder', 'approval.resolve'))
IDENTITY_FIELDS = ('caller_session_id', 'caller_execution_id', 'actor_session_id', 'execution_id', 'session_id_of_caller')
PROJECT = {'type': 'string', 'minLength': 1, 'maxLength': 300, 'description': '要操作的工作区名'}


def assistant_params(op):
    schema = copy.deepcopy(op.params)
    properties = {key: value for key, value in schema.get('properties', {}).items() if key not in IDENTITY_FIELDS}
    properties.setdefault('project', PROJECT)
    required = [key for key in schema.get('required', []) if key in properties]
    result = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        result['required'] = required
    return result


def covered(op):
    return ('human' in op.scopes and 'assistant' not in op.scopes and op.access in ('read', 'write')
            and op.name not in EXCLUDED and not op.name.startswith(EXCLUDED_PREFIXES))


for op in list(registry.operations().values()):
    if covered(op):
        registry.amend(op.name, scopes=op.scopes | {'assistant'}, assistant_as_user=True,
                       assistant_params=assistant_params(op),
                       risk='confirm' if op.access == 'write' and op.risk == 'normal' else op.risk)
