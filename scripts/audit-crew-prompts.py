"""Count observable Crew instruction text without launching AI or migrating data.

Usage: python scripts/audit-crew-prompts.py [--home HOME --session CREW_ID [--applied]]
Counts are Unicode characters, including whitespace/punctuation, never tokens.
The carrier's private system text and native tool catalog are not guessed.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin'))
from crew import behavior_prompts, codex_tools, rules


def measure(manifest, role):
    tools = codex_tools.tool_specs(assistant=role == 'assistant')
    text = behavior_prompts.render(manifest)
    return {'role': role, 'configured_prompt_characters': len(text),
            'prompt_sections': [
                {'key': item['key'], 'characters': len(item['content']),
                 'sha256': hashlib.sha256(item['content'].encode('utf-8')).hexdigest()}
                for item in manifest['prompts']],
            'crew_dynamic_tool_count': len(tools),
            'crew_dynamic_tools_json_characters': len(json.dumps(tools, ensure_ascii=False)),
            'native_identity_wrapper': 'not included; see captured thread/start or thread/resume request',
            'native_private_system_characters': None,
            'native_tool_catalog_characters': None}


def native_record(path):
    """Count recorded startup messages/tools, never print their text."""
    result = {'source': 'saved native startup record', 'developer_messages': [],
              'additional_tool_namespaces': [], 'exact_model_tokens': None}
    with path.open(encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            if index > 100:
                raise ValueError('No initial turn_context found; not counting the whole conversation as instructions')
            event = json.loads(line)
            data = event.get('payload') or {}
            if event.get('type') == 'turn_context':
                result['model'] = data.get('model')
                break
            if event.get('type') != 'response_item':
                continue
            if data.get('type') == 'message' and data.get('role') == 'developer':
                text = ''.join(item.get('text', '') for item in data.get('content', [])
                               if isinstance(item, dict) and isinstance(item.get('text', ''), str))
                result['developer_messages'].append({'characters': len(text),
                    'sha256': hashlib.sha256(text.encode()).hexdigest()})
            elif data.get('type') == 'additional_tools':
                tools = data.get('tools', [])
                result['additional_tools_json_characters'] = len(json.dumps(tools, ensure_ascii=False))
                result['additional_tool_namespaces'] = [item.get('name') for item in tools]
    result['developer_message_characters'] = sum(item['characters'] for item in result['developer_messages'])
    result['note'] = ('Developer text and serialized tool JSON have different formats; '
                      'do not add session_meta base/dynamic tools again or treat either as token counts.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--session')
    parser.add_argument('--applied', action='store_true', help='count the last saved general-chat delivery manifest')
    parser.add_argument('--rollout', type=Path, help='also count startup text from a saved native rollout, without printing text')
    args = parser.parse_args()
    if bool(args.home) != bool(args.session):
        parser.error('--home and --session must be supplied together')
    if args.applied and not args.home:
        parser.error('--applied requires --home and --session')
    if args.home:
        path = (args.home / 'crew.db').resolve()
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            session = dict(conn.execute('SELECT * FROM sessions WHERE session_id=?', (args.session,)).fetchone() or {})
            if not session:
                parser.error('Crew session does not exist in this HOME')
            role = rules.skill_role(conn, session)
            if args.applied:
                row = conn.execute('SELECT manifest_json,status,created_at FROM behavior_prompt_applications '
                    'WHERE execution_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (args.session,)).fetchone()
                if row is None:
                    parser.error('No saved general-chat delivery manifest for this session')
                result = measure(json.loads(row['manifest_json']), role)
                result['saved_delivery'] = {'status': row['status'], 'created_at': row['created_at']}
                results = [result]
            else:
                results = [measure(behavior_prompts.for_session(conn, args.session), role)]
    else:
        results = []
        for role in ('leader', 'member', 'assistant'):
            manifest = {'prompts': [{'key': key, 'content': behavior_prompts.DEFAULTS[key]}
                                   for key in ('common', role, 'report', 'context')]}
            results.append(measure(manifest, role))
    print(json.dumps({'measurement': 'Unicode characters including whitespace; not tokens',
                      'scope': 'Observable Crew configuration and Crew dynamic tools; not the full model context',
                      'prompt_source': 'saved delivery manifest' if args.applied else 'current source configuration',
                      'tool_schema_source': 'current source',
                      'ai_calls': 0, 'database_writes': 0, 'results': results,
                      'native_record': native_record(args.rollout) if args.rollout else None}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
