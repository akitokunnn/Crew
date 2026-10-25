'use strict';
// Real desktop + IPC + HTTP + filesystem acceptance, using a private HOME.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');

(async () => {
  const desktop = path.resolve(__dirname, '..');
  await fs.mkdir(path.join(desktop, '.cache'), {recursive:true});
  const home = await fs.mkdtemp(path.join(desktop, '.cache', 'storage-smoke-'));
  const output = path.join(desktop, 'smoke-output', 'storage');
  const env = {...process.env, PYTHONPATH:path.resolve(desktop, '../plugin'), PYTHONIOENCODING:'utf-8'};
  for (const name of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'ELECTRON_RUN_AS_NODE']) delete env[name];
  const seed = `import datetime as dt,json,os,sys
from pathlib import Path
from contextlib import closing
from crew import board,chat,session_ops,storage_cleanup,store
home=Path(sys.argv[1]); plugin=Path(sys.argv[2]); project=home.parent/(home.name+'-project')
project.mkdir(); (project/'keep.txt').write_text('project files stay unchanged',encoding='utf-8')
with closing(store.connect(home)) as conn:
 board.init_project(conn,'storage-demo',project)
 board.join(conn,'storage-fixture','storage-demo','资料验收',project,project,'main',role='leader')
 session_ops.archive(conn,'storage-fixture')
journal=chat.Journal(home/'chat'/'storage-fixture.jsonl')
referenced='11111111111111111111111111111111-1.png'; orphan='22222222222222222222222222222222-1.png'
journal.emit('message',id='fixture-message',role='user',text='保留有引用的附件',images=[referenced])
images=home/'chat'/'files'/'storage-fixture'; images.mkdir(parents=True)
paths=[home/'desktop'/'logs'/'startup-12345.log',images/referenced,images/orphan]
for index in range(4): paths.append(home/'backups'/('update-20260101-00000%d-abcdef'%index)/'crew.db')
for index,p in enumerate(paths):
 p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(('fixture-'+str(index)).encode()*128)
 stamp=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=100-index)).timestamp(); os.utime(p,(stamp,stamp))
manager=storage_cleanup.Manager(home,plugin)
manager.save_policy(dict(automatic_enabled=False,retention_days=30,keep_backups=3,recycle_days=7)); manager.close()
(home/'storage-fixture.json').write_text(json.dumps(dict(project=str(project),referenced=str(images/referenced),orphan=str(images/orphan),log=str(paths[0]),old_backup=str(paths[3]))),encoding='utf-8')
`;
  const prepared = spawnSync('python', ['-', home, path.resolve(desktop, '../plugin')], {input:seed, encoding:'utf8', env, windowsHide:true});
  assert.equal(prepared.status, 0, prepared.stderr || prepared.error?.message);
  const child = spawn(require('electron'), ['--require', path.join(__dirname, 'storage-smoke-hook.cjs'), desktop,
    '--smoke-test', '--home', home, '--port', '0', '--smoke-output', path.join(output, 'unused.json'), '--isolated-sources'],
    {cwd:desktop, env, windowsHide:true, stdio:['ignore', 'pipe', 'pipe']});
  child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(Error('Storage smoke timed out; see ' + output)); }, 180000);
    child.once('error', error => {clearTimeout(timer); reject(error);});
    child.once('exit', value => {clearTimeout(timer); resolve(value);});
  });
  const evidence = JSON.parse(await fs.readFile(path.join(output, 'evidence.json'), 'utf8'));
  assert.equal(code, 0, JSON.stringify(evidence.errors));
  assert.equal(evidence.ok, true, JSON.stringify(evidence));
  assert.equal(evidence.home, home);
  try {await fs.access(path.join(home, 'web.json')); assert.fail('Owned test service did not exit');}
  catch (error) {if (error.code !== 'ENOENT') throw error;}
  console.log(JSON.stringify({ok:true, checks:evidence.checks.length, evidence:path.join(output, 'evidence.json'), ownedServiceExited:true}));
})().catch(error => {console.error(error); process.exitCode = 1;});
