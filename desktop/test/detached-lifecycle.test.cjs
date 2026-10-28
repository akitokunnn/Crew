'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const core = require('../src/core.cjs');
const { Administrator } = require('../src/admin.cjs');

const plugin = path.resolve(__dirname, '../../plugin');
const coreFile = path.resolve(__dirname, '../src/core.cjs');
const comparablePath = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
function alive(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) return false;
  try { process.kill(pid, 0); return true; } catch (_) { return false; }
}
const environment = { ...process.env, PYTHONPATH: plugin, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' };
for (const key of ['CREW_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID', 'CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID']) delete environment[key];

const fakeBackend = String.raw`
import argparse,json,os,sys,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from crew.service_lock import HomeServiceLock
from crew import service_control
parser=argparse.ArgumentParser();parser.add_argument('--home');parser.add_argument('--port',type=int);parser.add_argument('--isolated-sources',action='store_true');args=parser.parse_args()
assert args.isolated_sources
home=Path(args.home).resolve();lock=HomeServiceLock(home)
if not lock.acquire():raise SystemExit(75)
counter=[0]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,status,data):
  body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path=='/api/projects':return self.reply(200,{'projects':[]})
  if self.path=='/api/admin/service':return self.reply(200,service_control.status(home))
  self.reply(200,{'ok':True,'service':'crew','home':str(home),'pid':os.getpid(),'workers':{'fixture':True},'warnings':[],'events':counter[0]})
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  if self.path!='/api/admin/service/stop' or self.headers.get('X-Crew')!='1':return self.reply(403,{'error':'fixture request rejected'})
  try:return self.reply(200,service_control.stop(home))
  except ValueError as error:return self.reply(409,{'error':str(error)})
server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.daemon_threads=True
url='http://127.0.0.1:'+str(server.server_port)
(home/'web.json').write_text(json.dumps({'url':url}),encoding='utf-8')
def advance():
 while True:counter[0]+=1;time.sleep(.04)
def stop():
 for line in sys.stdin:
  if line.strip()=='shutdown':break
 server.shutdown()
threading.Thread(target=advance,daemon=True).start();threading.Thread(target=stop,daemon=True).start()
print(url,flush=True)
try:server.serve_forever(poll_interval=.03)
finally:server.server_close();lock.release()
`;

async function until(check, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; }
    catch (error) { last = error; }
    await delay(30);
  }
  throw new Error('Timed out waiting for ' + label + (last ? ': ' + last.message : ''));
}

function childProcess(command, args) {
  const child = spawn(command, args, { cwd: plugin, env: environment, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  child.out = ''; child.errors = '';
  child.stdout.on('data', value => { child.out += value.toString(); });
  child.stderr.on('data', value => { child.errors += value.toString(); });
  child.once('error', error => { child.launchError = error; });
  return child;
}

async function exit(child) {
  await until(() => {
    if (child.launchError) throw child.launchError;
    return child.exitCode !== null;
  }, 'self-owned child exit; stderr=' + child.errors);
  assert.equal(child.exitCode, 0, child.errors);
}

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-detached-'));
  const home = path.join(root, 'home'), backend = path.join(root, 'backend');
  await fs.mkdir(home); await fs.mkdir(backend);
  await fs.writeFile(path.join(backend, 'fake_backend.py'), fakeBackend);
  const supervisor = `import argparse,sys\nfrom pathlib import Path\nfrom crew.supervisor import Supervisor\np=argparse.ArgumentParser();p.add_argument('--home');p.add_argument('--port',type=int);p.add_argument('--isolated-sources',action='store_true');a=p.parse_args()\nassert a.isolated_sources\nraise SystemExit(Supervisor(a.home,a.port,True,command_factory=lambda port:[sys.executable,str(Path(__file__).with_name('fake_backend.py'))],check_interval=.08,health_timeout=.3).run())\n`;
  await fs.writeFile(path.join(backend, 'crew_supervisor.py'), supervisor);
  const children = [];
  t.after(async () => {
    try {
      const current = await new Administrator(home).snapshot();
      if (!['stopped', 'unmanaged', 'unknown'].includes(current.state)) await new Administrator(home).request('shutdown');
    } catch (_) { /* A child may have exited before publishing its final status. */ }
    for (const child of children) {
      if (child.exitCode === null && child.stdin.writable) child.stdin.end('shutdown\n');
    }
    for (const child of children) if (child.exitCode === null) await exit(child);
    const statusFile = path.join(home, 'desktop', 'admin', 'status.json');
    try {
      const stopped = await until(async () => {
        const value = JSON.parse(await fs.readFile(statusFile, 'utf8'));
        return value.state === 'stopped' ? value : null;
      }, 'self-owned administrator cleanup', 10000);
      await until(() => !alive(stopped.supervisor_pid) && !alive(stopped.service_pid), 'self-owned process handles to exit', 10000);
    }
    catch (error) { if (error.message.includes('ENOENT')) { /* Startup never published. */ } else throw error; }
    // The only recursive removal is this test's verified mkdtemp directory.
    assert.equal(path.dirname(root), await fs.realpath(os.tmpdir()));
    assert(path.basename(root).startsWith('crew-detached-'));
    await fs.rm(root, { recursive: true, force: true });
  });
  return { root, home, backend, children,
    config: { home, backendDir: backend, python: 'python', port: 0, isolated: true, startupTimeoutMs: 15000 },
    status: async predicate => until(async () => {
      const status = JSON.parse(await fs.readFile(path.join(home, 'desktop', 'admin', 'status.json'), 'utf8'));
      return predicate(status) ? status : null;
    }, 'administrator state') };
}

test('a Node client can exit naturally after disconnect while its real supervisor and fixture work continue', { timeout: 45000 }, async t => {
  const current = await fixture(t), client = path.join(current.root, 'client.cjs');
  await fs.writeFile(client, `const core=require(${JSON.stringify(coreFile)});\n(async()=>{const backend=new core.Backend(${JSON.stringify(current.config)});const receipt=await backend.start();const health=await core.request(receipt.url,'/api/admin/health');await backend.disconnect();console.log(JSON.stringify({receipt,health,pid:process.pid}));})().catch(error=>{console.error(error);process.exitCode=1;});\n`);
  const started = Date.now(), child = childProcess(process.execPath, [client]);
  current.children.push(child);
  await exit(child);
  const record = JSON.parse(child.out.trim());
  assert.equal(record.receipt.owned, true);
  const first = await current.status(value => value.state === 'healthy');
  assert.equal(first.service_pid, record.health.pid);
  const advanced = await until(async () => {
    const health = await core.request(record.receipt.url, '/api/admin/health');
    return health.events > record.health.events ? health : null;
  }, 'fixture work after client exit');
  assert.equal(advanced.pid, record.health.pid);
  const second = new core.Backend(current.config, { spawn() { throw new Error('A second client must reuse the same service'); } });
  assert.deepEqual(await second.start(), { url: record.receipt.url, owned: false });
  await second.disconnect(); await second.stop();
  assert.equal((await core.request(second.url, '/api/admin/health')).pid, record.health.pid);
  assert.equal((await current.status(value => value.state === 'healthy')).supervisor_pid, first.supervisor_pid);
  const management = await core.request(second.url, '/api/admin/service');
  assert.equal(management.can_stop, true);
  assert.equal((await second.stopService()).status, 'requested');
  const stopped = await current.status(value => value.state === 'stopped');
  assert.equal(stopped.service_pid, record.health.pid);
  t.diagnostic(JSON.stringify({ kind: 'real-supervisor-fixture-backend', elapsed_ms: Date.now() - started,
    events_before_client_exit: record.health.events, events_after_client_exit: advanced.events,
    service_pid: record.health.pid, supervisor_pid: first.supervisor_pid, url: record.receipt.url,
    client_exit_code: child.exitCode, final_state: stopped.state }));
});

test('borrowed services survive client stop and supervisor shutdown and reject an unauthorized service stop', { timeout: 45000 }, async t => {
  const current = await fixture(t), started = Date.now();
  const backend = childProcess('python', [path.join(current.backend, 'fake_backend.py'), '--home', current.home, '--port', '0', '--isolated-sources']);
  current.children.push(backend);
  const url = await until(() => backend.out.split(/\r?\n/).find(line => /^http:\/\/127\.0\.0\.1:\d+$/.test(line)), 'standalone fixture URL');
  const owner = childProcess('python', [path.join(current.backend, 'crew_supervisor.py'), '--home', current.home, '--port', '0', '--isolated-sources']);
  current.children.push(owner);
  const first = await current.status(value => value.state === 'healthy' && value.ownership === 'borrowed');
  assert.equal(first.service_pid, backend.pid);
  const client = new core.Backend({ home: current.home, serviceUrl: url }, { spawn() { throw new Error('Must borrow'); } });
  assert.deepEqual(await client.start(), { url, owned: false });
  await client.stop(); await client.disconnect();
  assert.equal((await core.request(url, '/api/admin/health')).pid, backend.pid);
  assert.equal((await core.request(url, '/api/admin/service')).can_stop, false);
  await assert.rejects(client.stopService(), error => error.status === 409 && error.message.includes('不能停止另一实例'));
  assert.equal(backend.exitCode, null);
  owner.stdin.end('shutdown\n'); await exit(owner);
  assert.equal((await core.request(url, '/api/admin/health')).pid, backend.pid);
  assert.equal(backend.exitCode, null);
  backend.stdin.end('shutdown\n'); await exit(backend);
  t.diagnostic(JSON.stringify({ kind: 'real-supervisor-borrowed-fixture', elapsed_ms: Date.now() - started,
    service_pid: first.service_pid, supervisor_pid: first.supervisor_pid, url, stop_rejected: true,
    administrator_exit_code: owner.exitCode, final_backend_exit_code: backend.exitCode }));
});

test('the bundled pipe bridge releases an old custom supervisor and later stop waits for its real service', { timeout: 45000 }, async t => {
  const current = await fixture(t);
  const backend = new core.Backend({ ...current.config, helperBackendDir: plugin }, { frontendPipe: true,
    spawn: (command, args, options) => spawn(command, args, { ...options, env: { ...options.env, ...environment } }) });
  const receipt = await backend.start();
  const bridge = backend.child;
  const before = await core.request(receipt.url, '/api/admin/health');
  const administrator = await current.status(value => value.state === 'healthy');
  assert.notEqual(bridge.pid, administrator.supervisor_pid);
  await backend.disconnect();
  await until(() => bridge.exitCode === 0, 'the frontend pipe bridge to exit');
  assert.equal((await core.request(receipt.url, '/api/admin/health')).pid, before.pid);
  assert.equal((await current.status(value => value.state === 'healthy')).supervisor_pid, administrator.supervisor_pid);
  await backend.stop();
  await current.status(value => value.state === 'stopped');
  await until(() => !alive(before.pid) && !alive(administrator.supervisor_pid), 'the real administrator and service to exit');
  t.diagnostic(JSON.stringify({ kind: 'bundled-pipe-old-custom-source', bridge_pid: bridge.pid,
    administrator_pid: administrator.supervisor_pid, service_pid: before.pid, bridge_exit_code: bridge.exitCode,
    service_survived_bridge: true, explicit_stop_confirmed: true }));
});

test('the actual Crew service survives its frontend process exit and reconnects with the same instance and data', { timeout: 45000 }, async t => {
  const current = await fixture(t), client = path.join(current.root, 'real-client.cjs');
  const config = { ...current.config, backendDir: plugin };
  const workspace = path.join(current.root, 'ordinary-workspace');
  await fs.mkdir(workspace);
  await fs.writeFile(client, `const core=require(${JSON.stringify(coreFile)});\n(async()=>{const backend=new core.Backend(${JSON.stringify(config)});const receipt=await backend.start();const project=await core.request(receipt.url,'/api/project/init',{name:'detached-project',root:${JSON.stringify(workspace)}});const health=await core.request(receipt.url,'/api/admin/health');await backend.disconnect();console.log(JSON.stringify({receipt,project,health,pid:process.pid}));})().catch(error=>{console.error(error);process.exitCode=1;});\n`);
  const started = Date.now(), child = childProcess(process.execPath, [client]);
  current.children.push(child);
  await exit(child);
  const record = JSON.parse(child.out.trim());
  const first = await current.status(value => value.state === 'healthy');
  const health = await core.request(record.receipt.url, '/api/admin/health');
  assert.equal(health.instance_id, record.health.instance_id);
  assert.equal(health.pid, record.health.pid);
  assert.equal(path.resolve(health.home), path.resolve(current.home));
  assert.equal(path.resolve(health.plugin_root), plugin);
  assert.equal(health.version, require('../package.json').version);
  const second = new core.Backend(config, { spawn() { throw new Error('Must reconnect to actual service'); } });
  assert.deepEqual(await second.start(), { url: record.receipt.url, owned: false });
  await second.stop();
  const projects = await core.projects(record.receipt.url);
  assert.equal(projects.length, 1);
  assert.equal(projects[0].name, record.project.name);
  assert.equal(comparablePath(projects[0].root), comparablePath(workspace));
  await assert.rejects(fs.stat(path.join(workspace, '.git')), { code: 'ENOENT' });
  assert.equal((await second.stopService()).status, 'requested');
  await current.status(value => value.state === 'stopped');
  t.diagnostic(JSON.stringify({ kind: 'actual-crew-supervisor-and-service', elapsed_ms: Date.now() - started,
    service_pid: health.pid, supervisor_pid: first.supervisor_pid, url: record.receipt.url,
    instance_id: health.instance_id, version: health.version, workspace: projects[0].name,
    client_exit_code: child.exitCode, final_state: 'stopped' }));
});

test('actual Crew task scheduling advances a fake native carrier after the frontend has exited', { timeout: 45000 }, async t => {
  const current = await fixture(t), client = path.join(current.root, 'task-client.cjs');
  const fixtureEntry = path.join(plugin, 'tests', 'detached_service_fixture.py');
  const supervisorEntry = path.join(current.backend, 'crew_supervisor.py');
  await fs.writeFile(supervisorEntry, `import argparse,sys\nfrom crew.supervisor import Supervisor\np=argparse.ArgumentParser();p.add_argument('--home');p.add_argument('--port',type=int);p.add_argument('--isolated-sources',action='store_true');a=p.parse_args()\nassert a.isolated_sources\nraise SystemExit(Supervisor(a.home,a.port,True,command_factory=lambda port:[sys.executable,${JSON.stringify(fixtureEntry)}],check_interval=.08,health_timeout=.3).run())\n`);
  const workspace = path.join(current.root, 'task-workspace');
  await fs.mkdir(workspace);
  await fs.writeFile(client, `const core=require(${JSON.stringify(coreFile)});\n(async()=>{const backend=new core.Backend(${JSON.stringify(current.config)});const receipt=await backend.start();await core.request(receipt.url,'/api/settings',{ai:{default:{runtime:'claude',model:null,effort:null}}});await core.request(receipt.url,'/api/project/init',{name:'task-project',root:${JSON.stringify(workspace)}});const created=await core.request(receipt.url,'/api/tasks/create',{project:'task-project',text:'Record two synthetic results',acceptance:['Both synthetic results are recorded'],request_id:'detached-create'});const started=await core.request(receipt.url,'/api/tasks/start',{task_id:created.task.id,version:created.task.version,request_id:'detached-start'});await core.request(receipt.url,'/api/tasks/schedule',{task_id:created.task.id,version:started.task.version,session_id:created.task.leader_session_id,text:'Record the second synthetic result',evidence:{type:'user-instruction',text:'Record two synthetic results'},request_id:'detached-next'});const health=await core.request(receipt.url,'/api/admin/health');await backend.disconnect();console.log(JSON.stringify({receipt,task:created.task,health,pid:process.pid}));})().catch(error=>{console.error(error);process.exitCode=1;});\n`);
  const started = Date.now(), child = childProcess(process.execPath, [client]);
  current.children.push(child);
  await exit(child);
  const record = JSON.parse(child.out.trim());
  const before = (await fs.readFile(path.join(current.home, 'fake-carrier-inputs.jsonl'), 'utf8')).trim().split('\n');
  assert.equal(before.length, 1);
  // These fake native terminal events are deliberately released only after
  // the frontend's OS process exited, not merely after it hid its window.
  await fs.writeFile(path.join(current.home, 'release-turn-1'), 'release');
  const after = await until(async () => {
    const lines = (await fs.readFile(path.join(current.home, 'fake-carrier-inputs.jsonl'), 'utf8')).trim().split('\n');
    return lines.length === 2 ? lines : null;
  }, 'background task to dispatch its second authorized step');
  await fs.writeFile(path.join(current.home, 'release-turn-2'), 'release');
  const detail = await until(async () => {
    const value = await core.request(record.receipt.url, '/api/task?task_id=' + record.task.id);
    return value.scheduler.work.some(work => work.kind === 'work' && work.status === 'completed') ? value : null;
  }, 'background task work completion');
  const health = await core.request(record.receipt.url, '/api/admin/health');
  assert.equal(health.pid, record.health.pid);
  assert.equal(health.instance_id, record.health.instance_id);
  assert.equal(detail.task.id, record.task.id);
  const concreteWork = detail.scheduler.work.filter(work => work.kind === 'work');
  assert.equal(concreteWork.length, 1);
  assert.equal(concreteWork[0].status, 'completed');
  // This synthetic carrier reports terminal success without a plan or finish.
  // A bounded protocol check is expected; it must not replay the completed step.
  assert(detail.scheduler.work.every(work => work === concreteWork[0] || work.kind === 'protocol'));
  assert(detail.scheduler.work.filter(work => work.kind === 'protocol').length <= 1);
  const second = new core.Backend(current.config, { spawn() { throw new Error('Must reconnect'); } });
  assert.deepEqual(await second.start(), { url: record.receipt.url, owned: false });
  assert.equal((await second.stopService()).status, 'requested');
  await current.status(value => value.state === 'stopped');
  t.diagnostic(JSON.stringify({ kind: 'actual-crew-task-with-fake-native-carrier', elapsed_ms: Date.now() - started,
    service_pid: health.pid, url: record.receipt.url, instance_id: health.instance_id, task_id: record.task.id,
    native_inputs_before_client_exit: before.length, native_inputs_after_client_exit: after.length,
    work_status: concreteWork[0].status, protocol_checks: detail.scheduler.work.filter(work => work.kind === 'protocol').length,
    client_exit_code: child.exitCode, final_state: 'stopped' }));
});
