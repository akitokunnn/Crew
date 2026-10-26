'use strict';
// Actual desktop, backend HTTP and chat rendering against fixture journals only.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');

(async () => {
  const desktop = path.resolve(__dirname, '..');
  await fs.mkdir(path.join(desktop, '.cache'), {recursive:true});
  const home = await fs.mkdtemp(path.join(desktop, '.cache', 'handoff-smoke-'));
  const output = path.join(desktop, 'smoke-output', 'handoff');
  const env = {...process.env, PYTHONPATH:path.resolve(desktop, '../plugin'), PYTHONIOENCODING:'utf-8'};
  for (const name of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'ELECTRON_RUN_AS_NODE']) delete env[name];
  const seed = `import json,sys
from pathlib import Path
from contextlib import closing
from crew import board,chat,goals,runtimes,store,todo
home=Path(sys.argv[1]); project=home.parent/(home.name+'-project'); project.mkdir()
with closing(store.connect(home)) as conn:
 board.init_project(conn,'handoff-demo',project)
 for sid,name,role,group,runtime in (
  ('offline-claude','旧离线记录','leader',None,'claude'),
  ('handoff-codex','执行交接','leader',None,'codex'),
  ('failed-member','真实失败','member','执行交接','claude'),
  ('pending-member','下一轮工具','member','执行交接','codex')):
  board.join(conn,sid,'handoff-demo',name,project,project,'main',role=role,group_name=group)
  board.set_task(conn,sid,{'offline-claude':'核对当前协议与原始证据 · leader 工作目标',
                          'handoff-codex':'接续交接后的独立任务 · leader 工作目标',
                          'failed-member':'复核退出 23 的真实失败 · 成员本人任务',
                          'pending-member':'核对下一轮工具的真实结果 · 成员本人任务'}[sid])
  runtimes.set(conn,sid,runtime)
  board.set_status(conn,sid,'offline')
 board.set_leader_priority(conn,'handoff-demo',['handoff-codex'])
 goal=goals.create(conn,'handoff-demo','handoff-codex','交接后的独立任务','查看交接说明；验收不会启动任务。',['历史与真实回执保持可追溯'])
 todo.backfill(conn)
 conn.execute('UPDATE leader_notices SET sent_at=created_at')
def begin(sid,text):
 journal=chat.Journal(home/'chat'/(sid+'.jsonl'))
 journal.emit('message',id=sid+'-input',role='user',text=text)
 return journal
def tool(journal,identity,name,arguments):
 journal.emit('assistant',id=identity+'-assistant',content=[dict(type='tool_use',id=identity,name=name,input=arguments)])
old=begin('offline-claude','核对旧版会话留下的工具状态。')
old.set_status('working'); tool(old,'legacy-tool','Bash',dict(command='python verify.py\\n# 旧版记录没有收到工具结果'))
old.set_status('offline')
transfer=begin('handoff-codex','把当前工作转交到明确目标，保留真实状态。')
transfer.set_status('working'); tool(transfer,'handoff-tool','crew_goal',dict(action='start',goal_id=goal['id']))
transfer.emit('turn_result',status='interrupted',success=False,reason='identity_handoff')
transfer.set_status('offline'); transfer.emit('identity_handoff',session_id='handoff-codex',target_execution='fixture-execution',text='隔离夹具：旧执行已停止，独立任务状态另行查看。')
failed=begin('failed-member','保留真正失败的原因。')
failed.set_status('working'); tool(failed,'failed-tool','Bash',dict(command='python failing_case.py'))
failed.emit('turn_result',status='failed',success=False)
failed.emit('error',text='隔离夹具：子命令退出 23，原始失败原因仍然保留。'); failed.set_status('offline')
pending=begin('pending-member','上一轮结束后，新工具仍应等待真实结果。')
tool(pending,'prior-tool','Bash',dict(command='python previous_case.py'))
pending.emit('turn_result',status='completed',success=True); pending.set_status('idle')
tool(pending,'next-tool','Bash',dict(command='python next_case.py'))
# The final persisted status is intentionally idle: the unresolved tool is a
# rendering fixture, not permission to auto-resume a native/model process.
from crew.storage_service import StorageService
StorageService(home,isolated=True).refresh('handoff-demo',write_marker=True)
(home/'handoff-fixture.json').write_text(json.dumps(dict(project=str(project),goal_id=goal['id'])),encoding='utf-8')
`;
  const prepared = spawnSync('python', ['-', home], {input:seed, encoding:'utf8', env, windowsHide:true});
  assert.equal(prepared.status, 0, prepared.stderr || prepared.error?.message);
  // Install a subprocess-local fake carrier before the backend is imported.
  // Even a future fixture mistake cannot start an installed model CLI.
  const guard = path.join(home, 'fixture-guard');
  await fs.mkdir(guard);
  await fs.writeFile(path.join(guard, 'sitecustomize.py'), `import json
from pathlib import Path
from crew import chat
_original_init=chat.Manager.__init__
def _guarded_init(self,home,plugin_root,agents_provider,process_factory=None):
 def forbidden(*args,**kwargs):
  path=Path(home)/'fixture-model-start-attempts.txt'
  with path.open('a',encoding='utf-8') as stream: stream.write('Unexpected native start\\n')
  raise OSError('隔离验收禁止启动真实模型载体')
 _original_init(self,home,plugin_root,agents_provider,forbidden)
chat.Manager.__init__=_guarded_init
from crew import web
_original_state=web.Service.state
_fixture_home=Path(__file__).resolve().parent.parent
def _fixture_state(self,*args,**kwargs):
 result=_original_state(self,*args,**kwargs)
 if self.home.resolve()!=_fixture_home:
  return result
 path=self.home/'fixture-working.json'
 if path.is_file():
  fixture=json.loads(path.read_text(encoding='utf-8'))
  for role in ('leaders','members'):
   for person in result[role]:
    status=fixture.get(role,{}).get(person['session_id'])
    if status is not None:
     if status not in ('working','idle','waiting','offline'):
      raise ValueError('Invalid isolated working-status fixture')
     person['status']=status
 return result
web.Service.state=_fixture_state
`, 'utf8');
  env.PYTHONPATH = guard + path.delimiter + env.PYTHONPATH;
  const child = spawn(require('electron'), ['--require', path.join(__dirname, 'handoff-smoke-hook.cjs'), desktop,
    '--smoke-test', '--home', home, '--port', '0', '--smoke-output', path.join(output, 'unused.json'), '--isolated-sources'],
    {cwd:desktop, env, windowsHide:true, stdio:['ignore', 'pipe', 'pipe']});
  child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {child.kill(); reject(Error('Handoff smoke timed out; see ' + output));}, 180000);
    child.once('error', error => {clearTimeout(timer); reject(error);});
    child.once('exit', value => {clearTimeout(timer); resolve(value);});
  });
  const evidence = JSON.parse(await fs.readFile(path.join(output, 'evidence.json'), 'utf8'));
  assert.equal(code, 0, JSON.stringify(evidence.errors));
  assert.equal(evidence.ok, true, JSON.stringify(evidence));
  assert.equal(evidence.home, home);
  await assert.rejects(fs.access(path.join(home, 'web.json')), {code:'ENOENT'});
  console.log(JSON.stringify({ok:true, checks:evidence.checks.length, evidence:path.join(output, 'evidence.json'), ownedServiceExited:true}));
})().catch(error => {console.error(error); process.exitCode = 1;});
