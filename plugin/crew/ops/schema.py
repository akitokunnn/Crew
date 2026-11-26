"""Zero-dependency validator for the JSON Schema subset used by operations.

Supported keywords: type (string or list, incl. null/number), enum,
properties, required, additionalProperties (bool, default False), items,
minLength (>=1 also rejects blank text), maxLength, minimum, maximum,
minItems, maxItems, description. Anything else is a registry error.
"""
import json

from .errors import OpError

KEYWORDS = frozenset(('type', 'enum', 'properties', 'required', 'additionalProperties', 'items', 'minLength',
                      'maxLength', 'minimum', 'maximum', 'minItems', 'maxItems', 'description', 'default'))
_TYPES = {
    'object': lambda v: isinstance(v, dict),
    'array': lambda v: isinstance(v, list),
    'string': lambda v: isinstance(v, str),
    'boolean': lambda v: type(v) is bool,
    'integer': lambda v: type(v) is int,
    'number': lambda v: type(v) in (int, float),
    'null': lambda v: v is None,
}


def check(schema, path='params'):
    """Reject schema keywords the validator would silently ignore."""
    if not isinstance(schema, dict):
        raise ValueError(path + ' must be a schema object')
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise ValueError('%s uses unsupported schema keywords: %s' % (path, ', '.join(sorted(unknown))))
    kinds = schema.get('type')
    for kind in (kinds if isinstance(kinds, list) else [kinds]):
        if kind not in _TYPES:
            raise ValueError('%s has unsupported type %r' % (path, kind))
    for key, item in schema.get('properties', {}).items():
        check(item, path + '.' + key)
    if 'items' in schema:
        check(schema['items'], path + '[]')
    missing = set(schema.get('required', ())) - set(schema.get('properties', {}))
    if missing and schema.get('additionalProperties') is not True:
        raise ValueError('%s requires undeclared fields: %s' % (path, ', '.join(sorted(missing))))


def validate(value, schema, path='arguments'):
    kinds = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
    if not any(_TYPES[kind](value) for kind in kinds):
        raise OpError('invalid_arguments', '%s 必须是 %s' % (path, ' 或 '.join(kinds)))
    if 'enum' in schema and value not in schema['enum']:
        raise OpError('invalid_arguments', '%s 只能取 %s' % (path, '、'.join(map(str, schema['enum']))))
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is not True:
            extra = sorted(key for key in value if key not in properties)
            if extra:
                raise OpError('invalid_arguments', '%s 含不支持的字段：%s' % (path, '、'.join(extra)))
        missing = [key for key in schema.get('required', ()) if key not in value]
        if missing:
            raise OpError('invalid_arguments', '%s 缺少必填字段：%s' % (path, '、'.join(missing)))
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], path + '.' + key)
        if schema.get('additionalProperties') is True:
            try:
                json.dumps(value, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise OpError('invalid_arguments', path + ' 必须是有限的 JSON 数据') from None
    elif isinstance(value, str):
        if schema.get('minLength', 0) >= 1 and not value.strip():
            raise OpError('invalid_arguments', path + ' 不能为空')
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 20000):
            raise OpError('invalid_arguments', path + ' 长度超出范围')
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 500):
            raise OpError('invalid_arguments', path + ' 项数超出范围')
        if 'items' in schema:
            for index, item in enumerate(value):
                validate(item, schema['items'], '%s[%d]' % (path, index))
    elif type(value) in (int, float):
        if value < schema.get('minimum', float('-inf')) or value > schema.get('maximum', float('inf')):
            raise OpError('invalid_arguments', path + ' 超出取值范围')
