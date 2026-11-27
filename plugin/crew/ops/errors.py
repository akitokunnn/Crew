"""Structured operation errors (design §5.7)."""

CODES = frozenset(('forbidden', 'invalid_arguments', 'not_found', 'conflict', 'request_id_reused',
                   'awaiting_user', 'paused_for_update', 'unknown_outcome', 'internal'))
HTTP_STATUS = {'forbidden': 403, 'invalid_arguments': 400, 'not_found': 404, 'conflict': 409,
               'request_id_reused': 409, 'awaiting_user': 202, 'paused_for_update': 409,
               'unknown_outcome': 409, 'internal': 500}


class OpError(Exception):
    def __init__(self, code, message, detail=None):
        if code not in CODES:
            raise ValueError('Unknown operation error code: ' + code)
        self.code, self.detail = code, detail or {}
        super().__init__(message)

    def payload(self):
        return {'error': {'code': self.code, 'message': str(self), 'detail': self.detail}}
