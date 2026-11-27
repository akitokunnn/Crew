"""Structured operation errors (design §5.7)."""

PUBLIC_CODES = {'project_unavailable': 404, 'not_visible': 404, 'version_conflict': 409,
                'request_conflict': 409, 'outcome_unknown': 202, 'room_occupied': 409, 'invalid_plan': 400,
                'missing_recommendation': 400, 'invalid_criteria': 400, 'criteria_incomplete': 409}
ALIASES = {'request_id_reused': 'request_conflict', 'unknown_outcome': 'outcome_unknown'}
CODES = frozenset(PUBLIC_CODES) | frozenset(('forbidden', 'invalid_arguments', 'not_found', 'conflict', 'request_id_reused',
                   'awaiting_user', 'paused_for_update', 'unknown_outcome', 'internal'))
HTTP_STATUS = {**PUBLIC_CODES, 'forbidden': 403, 'invalid_arguments': 400, 'not_found': 404, 'conflict': 409,
               'request_id_reused': 409, 'awaiting_user': 202, 'paused_for_update': 409,
               'unknown_outcome': 409, 'internal': 500}


class OpError(Exception):
    def __init__(self, code, message, detail=None):
        if code not in CODES:
            raise ValueError('Unknown operation error code: ' + code)
        self.code, self.detail = code, detail or {}
        super().__init__(message)

    def public_code(self):
        code = ALIASES.get(self.code, self.code)
        return {'error_code': code} if code in PUBLIC_CODES else {}

    def payload(self):
        return {'error': {'code': self.code, 'message': str(self), 'detail': self.detail}, **self.public_code()}
