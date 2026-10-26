"""Isolated UI fixture, not a production launcher. Model/process starts are fatal."""
from contextlib import closing
import datetime
import json
from pathlib import Path
import sys
import threading
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugin'))
from crew.storage_v2 import Repository, now_iso
from crew import web

OUTPUT = Path(sys.argv[1]).resolve()
HOME = OUTPUT / 'home'
assert OUTPUT.is_relative_to(ROOT / 'desktop' / 'smoke-output' / 'history')
HOME.mkdir(parents=True, exist_ok=True)
repo = Repository(HOME)
workspace = repo.create_workspace('股市预警 · UI隔离验收')
wid = workspace['id']
namespace = uuid.UUID(wid)
identity = lambda text: str(uuid.uuid5(namespace, text))
base = datetime.datetime(2026, 10, 25, 0, 0, tzinfo=datetime.timezone.utc)
stamp = lambda offset: (base + datetime.timedelta(seconds=offset)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
leader, conversation = identity('leader'), identity('main-conversation')
html = b'<!doctype html><meta charset="utf-8"><h1>Saved delivery proof</h1><p>HTML bytes survived the package boundary.</p><script>document.documentElement.dataset.scriptExecuted="1";fetch("https://invalid.example/should-never-run")</script>'
attachment = 'Attachment bytes proof / 附件确实可读'.encode('utf-8')
html_info = repo.objects.put_bytes(wid, html)
attachment_info = repo.objects.put_bytes(wid, attachment)
html_blob = repo.register_blob(wid, html_info, 'text/html')
attachment_blob = repo.register_blob(wid, attachment_info, 'text/plain; charset=utf-8')
artifact, version = identity('report-artifact'), identity('report-version')

with repo.transaction() as conn:
    def add(table, **values):
        names = {row['name'] for row in conn.execute('PRAGMA table_info(' + table + ')')}
        if 'created_at' in names: values.setdefault('created_at', stamp(0))
        if 'updated_at' in names: values.setdefault('updated_at', values.get('created_at', stamp(0)))
        values.setdefault('workspace_id', wid)
        conn.execute('INSERT INTO '+table+'('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', tuple(values.values()))
    add('agents', id=leader, name='行情 Leader', created_at=stamp(2000))
    add('groups', id=identity('group'), name='行情组', leader_agent_id=leader)
    for n in range(204):
        agent = identity('member-' + str(n))
        add('agents', id=agent, name='成员 %03d' % (n+1), created_at=stamp(n+1))
        add('conversations', id=identity('extra-conversation-'+str(n)), kind='direct', title='归档会话 %03d' % (n+1), owner_agent_id=agent, created_at=stamp(n+1))
    add('conversations', id=conversation, kind='direct', title='会话主线 · 120条', owner_agent_id=leader, created_at=stamp(2000))
    for n in range(1,121):
        message = identity('message-' + str(n))
        add('messages', id=message, conversation_id=conversation, author_kind='agent', author_agent_id=leader,
            author_name_snapshot='行情 Leader', kind='assistant', created_at=stamp(0))
        add('message_positions', id=identity('position-'+str(n)), conversation_id=conversation, message_id=message, accepted_sequence=n)
        add('message_parts', id=identity('part-'+str(n)), message_id=message, part_index=0, kind='text', text_content='消息 #%03d' % n)
        if n == 120:
            add('message_parts', id=identity('attachment-part'), message_id=message, part_index=1, kind='file', blob_id=attachment_blob['id'])
    add('artifacts', id=artifact, name='验收报告 HTML', kind='report', current_version_id=version)
    add('artifact_versions', id=version, artifact_id=artifact, version_no=1, blob_id=html_blob['id'],
        display_filename='proof.html', mime_type='text/html', author_agent_id=leader)
    for n in range(1,216):
        add('reports', id=identity('report-'+str(n)), author_agent_id=leader, kind='phase', title='历史报告 #%03d' % n,
            summary='可读历史与报告分页验收', outcome='informational', body_markdown='已保存的报告正文 #%03d' % n,
            data_json='{}', html_artifact_version_id=version, created_at=stamp(n))

process_calls = []
def forbid_process(*args, **kwargs):
    process_calls.append('attempted process start')
    raise AssertionError('The history UI must never start an AI/native process')

with mock.patch('subprocess.Popen', forbid_process), mock.patch.object(web.Service, 'start', lambda self: None):
    server = web.create_server(HOME, ROOT/'plugin', port=0, agents_provider=lambda: [],
        claude_root=HOME/'isolated'/'claude', codex_root=HOME/'isolated'/'codex',
        runs_file=HOME/'isolated'/'runs.jsonl', vscode_state_db=HOME/'isolated'/'vscode.db',
        cwd=HOME, isolated_sources=True)
    assert server.server_port not in (7122, 47811)
    metadata = dict(url=server.url, workspace_id=wid, home=str(HOME), leader_id=leader,
                    conversation_id=conversation, version_id=version, blob_id=attachment_blob['id'],
                    counts=dict(agents=205, conversations=205, messages=120, reports=215),
                    scope='real isolated HTTP and renderer, no background model workers')
    (OUTPUT/'fixture.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps(metadata,ensure_ascii=False),flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == 'shutdown': break
    finally:
        server.shutdown(); thread.join(10); server.server_close()
        evidence = dict(model_process_starts=len(process_calls), calls=process_calls, owned_server_exited=True)
        (OUTPUT/'backend-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        if process_calls: raise AssertionError(process_calls)
