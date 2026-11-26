"""``crew_search``: find operations by Chinese intent or name (design §5.3)."""
import json
import re

LIMIT = 8
RISK_NOTE = {'normal': '', 'confirm': '全局助手调用需附触发它的用户消息 message_id',
             'high': '高风险：AI 调用会生成用户确认请求，用户在界面确认后才执行'}


def _grams(text):
    text = text.lower()
    words = set(re.findall(r'[a-z0-9_]+', text))
    han = re.sub(r'[^一-鿿]', '', text)
    return words | set(han) | {han[i:i + 2] for i in range(len(han) - 1)}


def _score(op, query, grams):
    name = op.name.lower()
    if query in (name, op.tool_name):
        return 1000
    text = ' '.join((op.name.replace('.', ' '), op.tool_name, op.summary, op.ai_description or op.description))
    haystack = _grams(text)
    score = sum(3 if len(gram) > 1 else 1 for gram in grams & haystack)
    if query and query in text.lower():
        score += 20
    return score


def describe(op, caller, exposure='core'):
    params = json.loads(json.dumps(op.schema_for(caller)))
    if op.access != 'read':
        params.setdefault('properties', {})['request_id'] = {'type': 'string', 'description': '本次操作的稳定编号'}
        params['required'] = list(params.get('required', [])) + ['request_id']
    if op.risk == 'confirm' and caller.role == 'assistant':
        params.setdefault('properties', {})['message_id'] = {'type': 'string', 'description': '触发它的用户消息编号'}
        params['required'] = list(params.get('required', [])) + ['message_id']
    how = ('用 crew_call 调用：op="%s"，args 按 params 填写' % op.name if caller.runtime == 'codex' and exposure == 'core'
           else '直接调用工具 %s' % op.tool_name)
    return {'op': op.name, 'tool': op.tool_name, 'summary': op.summary, 'when': op.description_for(caller),
            'access': op.access, 'risk': RISK_NOTE[op.risk] or None, 'params': params, 'how': how}


def search(ops, query, domain, caller, exposure='core'):
    query = (query or '').strip().lower()
    grams = _grams(query)
    pool = [op for op in ops if domain is None or op.name.split('.')[0] == domain]
    ranked = sorted(((_score(op, query, grams), op.name, op) for op in pool), key=lambda item: (-item[0], item[1]))
    return [describe(op, caller, exposure) for score, _name, op in ranked[:LIMIT] if score > 0]
