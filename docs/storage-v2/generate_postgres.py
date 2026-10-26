"""Generate matching PostgreSQL domain DDL from the trusted SQLite design.

TEXT UUIDs are deliberate in both engines. INTEGER becomes BIGINT to retain
SQLite's signed 64-bit range. Foreign keys are added after all tables exist.
This is not a database migration and never connects to a real server.
"""
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3

DIRECTORY = Path(__file__).resolve().parent


def split_definitions(body):
    definitions, start, depth, quoted, index = [], 0, 0, False, 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if quoted and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ',' and depth == 0:
                definitions.append(body[start:index].strip())
                start = index + 1
        index += 1
    definitions.append(body[start:].strip())
    return definitions


def expression(value):
    value = re.sub(r'\bINTEGER\b', 'BIGINT', value)
    value = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', value)
    value = re.sub(r'\bjson_valid\s*\(', 'crew_json_valid(', value)
    value = re.sub(r"replace\((\w+),'-',''\) NOT GLOB '\*\[\^0-9a-f\]\*'", r"replace(\1,'-','') !~ '[^0-9a-f]'", value)
    value = re.sub(r"\b(\w+) NOT GLOB '\*\[\^0-9a-f\]\*'", r"\1 !~ '[^0-9a-f]'", value)
    value = value.replace("GLOB '????-??-??T??:??:??*Z'", "LIKE '____-__-__T__:__:__%Z'")
    # SQLite treats predicates as 0/1; PostgreSQL requires explicit arithmetic.
    addition = r'\([\w.]+ IS NOT NULL\)(?:\s*\+\s*\([\w.]+ IS NOT NULL\))+'
    def numeric_predicates(match):
        return re.sub(r'\(([\w.]+) IS NOT NULL\)', r'(CASE WHEN \1 IS NOT NULL THEN 1 ELSE 0 END)', match.group())
    value = re.sub(addition, numeric_predicates, value)
    value = re.sub(r'(NEW\.\w+) IS NOT (OLD\.\w+)', r'\1 IS DISTINCT FROM \2', value)
    if re.search(r'\bGLOB\b|\bRAISE\s*\(|\bAUTOINCREMENT\b|\btypeof\s*\(', value, re.I):
        raise ValueError('Unconverted SQLite expression: ' + value)
    return value


def render():
    source = (DIRECTORY / 'sqlite.sql').read_bytes()
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(source.decode('utf-8'))
        schema = list(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY rowid"))
        output = [
            '-- Generated design DDL; run generate_postgres.py after sqlite.sql changes.',
            '-- Source SHA-256: ' + hashlib.sha256(source).hexdigest(),
            '-- Apply this DOMAIN file before cloud.sql in a dedicated database/schema.',
            '-- UUIDs remain canonical TEXT in both engines; API UUID validation also applies.',
            '-- No PostgreSQL server is contacted by this generator.',
            'BEGIN;',
            """CREATE OR REPLACE FUNCTION crew_json_valid(value TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    PERFORM value::json;
    RETURN TRUE;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN FALSE;
END;
$$;""",
        ]
        foreign_keys = []
        for row in schema:
            if row['type'] != 'table':
                continue
            body = row['sql'][row['sql'].index('(') + 1:row['sql'].rindex(')')]
            definitions = []
            for item in split_definitions(body):
                if item.upper().startswith('FOREIGN KEY'):
                    continue
                item = re.sub(r'\s+REFERENCES\s+\w+\s*\([^)]*\)(?:\s+DEFERRABLE\s+INITIALLY\s+DEFERRED)?', '', item, flags=re.I)
                definitions.append(expression(item))
            output.append('CREATE TABLE IF NOT EXISTS ' + row['name'] + ' (\n    ' + ',\n    '.join(definitions) + '\n);')
            groups = {}
            for foreign in conn.execute('PRAGMA foreign_key_list("' + row['name'] + '")'):
                groups.setdefault(foreign['id'], []).append(dict(foreign))
            for number, group in sorted(groups.items()):
                group.sort(key=lambda entry: entry['seq'])
                parent = group[0]['table']
                name = 'fk_' + row['name'] + '_' + str(number)
                columns = ','.join(item['from'] for item in group)
                referenced = ','.join(item['to'] for item in group)
                foreign_keys.append('ALTER TABLE ' + row['name'] + ' ADD CONSTRAINT ' + name + ' FOREIGN KEY (' + columns + ') REFERENCES ' + parent + '(' + referenced + ') DEFERRABLE INITIALLY DEFERRED;')
        output.extend(foreign_keys)
        for row in schema:
            if row['type'] == 'index' and row['sql']:
                output.append(expression(row['sql']) + ';')
            elif row['type'] == 'trigger':
                match = re.fullmatch(r"CREATE TRIGGER(?: IF NOT EXISTS)? (\w+)\s+BEFORE UPDATE ON (\w+)\s+(?:WHEN (.*?)\s+)?BEGIN\s+SELECT RAISE\(ABORT, '((?:''|[^'])*)'\);\s+END", row['sql'], flags=re.S)
                if not match:
                    raise ValueError('Unconverted trigger: ' + row['name'])
                name, table, condition, message = match.groups()
                function = name + '_fn'
                output.append("CREATE OR REPLACE FUNCTION " + function + "() RETURNS TRIGGER LANGUAGE plpgsql AS $$\nBEGIN\n    RAISE EXCEPTION '" + message + "';\nEND;\n$$;")
                output.append('DROP TRIGGER IF EXISTS ' + name + ' ON ' + table + ';')
                when = ' WHEN (' + expression(condition) + ')' if condition else ''
                output.append('CREATE TRIGGER ' + name + ' BEFORE UPDATE ON ' + table + ' FOR EACH ROW' + when + ' EXECUTE FUNCTION ' + function + '();')
        output.append('COMMIT;')
        return '\n\n'.join(output) + '\n'


if __name__ == '__main__':
    target = DIRECTORY / 'postgres-domain.sql'
    target.write_text(render(), encoding='utf-8', newline='\n')
    print('Generated ' + target.name)
