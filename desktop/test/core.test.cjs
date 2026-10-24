'use strict';
const test = require('node:test'); const assert = require('node:assert/strict');
const fs = require('node:fs/promises'); const path = require('node:path'); const os = require('node:os');
const http = require('node:http'); const { execFile } = require('node:child_process'); const { promisify } = require('node:util');
const core = require('../src/core.cjs'); const run = promisify(execFile);
async function directory(t) { const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-desktop-test-')); t.after(() => fs.rm(root, { recursive: true, force: true })); return root; }
async function fixture(t, known = []) {
  const calls = []; const server = http.createServer(async (request, response) => {
    calls.push(request.url); response.setHeader('Content-Type', 'application/json');
    if (request.url === '/api/projects') { response.end(JSON.stringify({ projects: known })); return; }
    if (request.url === '/api/project/init') {
      let body = ''; for await (const part of request) body += part;
      const project = JSON.parse(body); known.push(project); response.end(JSON.stringify(project)); return;
    }
    response.writeHead(404); response.end(JSON.stringify({ error: 'missing' }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { url: `http://127.0.0.1:${server.address().port}`, known, calls };
}

test('service URLs reject remote hosts, credentials, redirects paths and untrusted protocols', () => {
  assert.equal(core.loopback('http://127.0.0.1:1234/'), 'http://127.0.0.1:1234');
  for (const value of ['https://example.com/', 'http://example.com:1234/', 'file:///x', 'http://user@127.0.0.1:1234/', 'http://127.0.0.1:1234/api/state', 'http://127.0.0.1:1234/?x=1']) assert.throws(() => core.loopback(value));
});
test('smoke requires explicit isolated home and cannot bind the production port', () => {
  assert.throws(() => core.options(['--smoke-test']));
  assert.throws(() => core.options(['--smoke-test', '--home', 'test', '--smoke-output', 'test.json', '--port', '47811']));
  assert.throws(() => core.options(['--port', '-1']));
  assert.equal(core.options(['--home', 'test', '--port', '0']).port, 0);
});
test('new workspace name is a filename rather than an arbitrary path', () => {
  for (const name of ['../escape', 'one/two', 'one\\two', '.', '..', 'CON', 'LPT1.txt', 'name.', '', ' x ']) assert.throws(() => core.workspaceName(name));
  assert.equal(core.workspaceName('我的新项目'), '我的新项目');
});
test('existing repository and nested folder register canonical git root without altering files', async t => {
  const root = await directory(t), service = await fixture(t); const repo = path.join(root, '已有项目');
  await fs.mkdir(repo); await run('git', ['init', repo], { windowsHide: true }); await fs.mkdir(path.join(repo, 'src'));
  await fs.writeFile(path.join(repo, 'keep.txt'), 'unchanged');
  const project = await core.existingWorkspace(path.join(repo, 'src'), service.url);
  assert.equal(project.root, await fs.realpath(repo)); assert.equal(project.name, '已有项目');
  assert.equal(await fs.readFile(path.join(repo, 'keep.txt'), 'utf8'), 'unchanged');
  assert.deepEqual(await core.existingWorkspace(repo, service.url), project);
  assert.equal(service.calls.filter(value => value === '/api/project/init').length, 1);
});
test('a folder without Git is rejected before registration', async t => {
  const root = await directory(t), service = await fixture(t);
  await assert.rejects(core.existingWorkspace(root, service.url)); assert.equal(service.known.length, 0);
});
test('new workspace creates an empty main-branch Git repository then registers it', async t => {
  const root = await directory(t), service = await fixture(t);
  const project = await core.newWorkspace(root, 'new-project', service.url);
  assert.equal(project.name, 'new-project');
  const branch = await run('git', ['-C', project.root, 'symbolic-ref', '--short', 'HEAD'], { windowsHide: true });
  assert.equal(branch.stdout.trim(), 'main');
  await assert.rejects(core.newWorkspace(root, 'new-project', service.url), /EEXIST/);
  assert.equal(service.known.length, 1);
});
test('healthy registry service is reused and never spawned or stopped by the desktop', async t => {
  const root = await directory(t), service = await fixture(t);
  await fs.writeFile(path.join(root, 'web.json'), JSON.stringify({ url: service.url }));
  const backend = new core.Backend({ home: root }, { spawn: () => { throw new Error('must not spawn'); } });
  assert.deepEqual(await backend.start(), { url: service.url, owned: false }); await backend.stop();
  assert.deepEqual(await core.projects(service.url), []);
  assert.equal(JSON.parse(await fs.readFile(path.join(root, 'web.json'), 'utf8')).url, service.url);
});

test('explicit service is borrowed without changing another service registry', async t => {
  const root = await directory(t), selected = await fixture(t), other = await fixture(t);
  const registry = JSON.stringify({ url:other.url }); await fs.writeFile(path.join(root,'web.json'),registry);
  const backend = new core.Backend({ home:root, serviceUrl:selected.url }, { spawn:()=>{ throw new Error('must not spawn'); } });
  assert.deepEqual(await backend.start(), { url:selected.url, owned:false }); await backend.stop();
  assert.deepEqual(await core.projects(selected.url), []);
  assert.equal(other.calls.length, 0); assert.equal(await fs.readFile(path.join(root,'web.json'),'utf8'),registry);
});

test('failed explicit service refuses registry fallback and never launches another backend', async t => {
  const root = await directory(t), other = await fixture(t), invalid = http.createServer((_request,response)=>{response.writeHead(503);response.end('{"error":"offline"}');});
  await new Promise(resolve=>invalid.listen(0,'127.0.0.1',resolve)); t.after(()=>new Promise(resolve=>invalid.close(resolve)));
  const registry = JSON.stringify({ url:other.url }); await fs.writeFile(path.join(root,'web.json'),registry);
  const backend = new core.Backend({ home:root, serviceUrl:`http://127.0.0.1:${invalid.address().port}` }, { spawn:()=>{ throw new Error('must not spawn'); } });
  await assert.rejects(backend.start(), /指定的 Crew 服务不可用/);
  assert.equal(backend.child, null); assert.equal(backend.owned, false); assert.equal(other.calls.length, 0);
  assert.equal(await fs.readFile(path.join(root,'web.json'),'utf8'),registry);
});

test('UI profiles have separate local lock paths and cannot escape their home', () => {
  const normal = core.options(['--home','test']), separate = core.options(['--home','test','--profile','ui-011']);
  assert.equal(normal.userData,path.join(normal.home,'desktop','ui'));
  assert.equal(separate.userData,path.join(separate.home,'desktop','profiles','ui-011'));
  assert.notEqual(normal.userData,separate.userData);
  for (const name of ['../escape','C:\\other','CON','ui one','ui/other']) assert.throws(()=>core.options(['--profile',name]));
  assert.throws(()=>core.options(['--service-url','https://example.com/']));
  assert.throws(()=>core.options(['--smoke-visual']));
  assert.equal(core.options(['--service-url=http://127.0.0.1:12345','--profile=ui-new']).serviceUrl,'http://127.0.0.1:12345');
  assert.equal(core.options(['--service-url=http://127.0.0.1:12345','--profile=ui-new']).profile,'ui-new');
});

test('connected incompatible registry service never starts a second backend', async t => {
  const root=await directory(t), server=http.createServer((_request,response)=>{response.setHeader('Content-Type','application/json');response.end('{"something":"else"}');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve)); t.after(()=>new Promise(resolve=>server.close(resolve)));
  await fs.writeFile(path.join(root,'web.json'),JSON.stringify({url:`http://127.0.0.1:${server.address().port}`}));
  const backend=new core.Backend({home:root},{spawn:()=>{throw new Error('must not spawn');}});
  await assert.rejects(backend.start(),/现有 Crew 服务暂时不可用/); assert.equal(backend.child,null);
});

test('ambiguous registry timeout never starts a second backend', async t => {
  const root=await directory(t); await fs.writeFile(path.join(root,'web.json'),'{"url":"http://127.0.0.1:12345"}');
  const original=global.fetch; global.fetch=async()=>{throw new DOMException('test timeout','TimeoutError');};
  t.after(()=>{global.fetch=original;});
  const backend=new core.Backend({home:root},{spawn:()=>{throw new Error('must not spawn');}});
  await assert.rejects(backend.start(),/现有 Crew 服务暂时不可用/); assert.equal(backend.child,null);
});

test('a timeout can delegate startup only after the administrator independently confirms the exact old service is stopped', async t => {
  const root=await directory(t), service=await fixture(t), backendDir=path.join(root,'backend');
  await fs.mkdir(backendDir);
  const old='http://127.0.0.1:12345';
  await fs.writeFile(path.join(root,'web.json'),JSON.stringify({url:old}));
  const code='import sys,json\nif "--probe" in sys.argv:\n print(json.dumps({"can_start": True, "url": '+JSON.stringify(old)+'}),flush=True)\nelse:\n print('+JSON.stringify(service.url)+',flush=True)\n sys.stdin.readline()\n';
  await fs.writeFile(path.join(backendDir,'crew_supervisor.py'),code);
  const original=global.fetch;global.fetch=async(url,...args)=>{if(String(url).startsWith(old+'/'))throw new DOMException('timeout','TimeoutError');return original(url,...args);};
  t.after(()=>{global.fetch=original;});
  const backend=new core.Backend({home:root,backendDir,python:'python',port:0,isolated:true,smoke:true});
  t.after(()=>backend.stop());
  assert.deepEqual(await backend.start(),{url:service.url,owned:true});
  assert.equal(backend.supervised,true);
  await backend.stop();assert.equal(backend.child.exitCode,0);
});

test('an administrator probe with an uncertain PID or a different URL cannot authorize startup after a timeout',async t=>{
  const root=await directory(t),backendDir=path.join(root,'backend');await fs.mkdir(backendDir);
  await fs.writeFile(path.join(root,'web.json'),'{"url":"http://127.0.0.1:12345"}');
  const original=global.fetch;global.fetch=async()=>{throw new DOMException('timeout','TimeoutError');};
  t.after(()=>{global.fetch=original;});
  for(const probe of [{can_start:false,url:'http://127.0.0.1:12345'},{can_start:true,url:'http://127.0.0.1:12346'}]){
    await fs.writeFile(path.join(backendDir,'crew_supervisor.py'),'print('+JSON.stringify(JSON.stringify(probe))+')\n');
    const backend=new core.Backend({home:root,backendDir,python:'python'},{spawn:()=>{throw Error('must not spawn');}});
    await assert.rejects(backend.start(),/现有 Crew 服务暂时不可用/);assert.equal(backend.child,null);
  }
});

test('malformed registry is reported without spawning a replacement', async t => {
  const root=await directory(t); await fs.writeFile(path.join(root,'web.json'),'{invalid json');
  const backend=new core.Backend({home:root},{spawn:()=>{throw new Error('must not spawn');}});
  await assert.rejects(backend.start(),/无法读取 Crew 服务登记/); assert.equal(backend.child,null);
});

test('a supervised startup timeout reaches recovery without killing or waiting for a hung backend shutdown',async t=>{
  const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
  const root=await directory(t),backendDir=path.join(root,'backend');await fs.mkdir(backendDir);
  await fs.writeFile(path.join(backendDir,'crew_supervisor.py'),'# isolated fake supervisor');
  const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();child.exitCode=null;
  let kills=0,written='';child.kill=()=>{kills++;};child.stdin.on('data',chunk=>written+=chunk);
  const backend=new core.Backend({home:root,backendDir,python:'python',port:0,startupTimeoutMs:25},{spawn:()=>child});
  const began=Date.now();await assert.rejects(backend.start(),/服务启动超时/);
  assert(Date.now()-began<1000,'A hung supervisor shutdown must not block the recovery UI');
  assert.equal(kills,0);assert.equal(written,'');assert.equal(backend.owned,true);
  // Explicit quit still requests shutdown and waits for the real exit.
  const stopping=backend.stop();assert.equal(written,'shutdown\n');
  child.exitCode=0;child.emit('exit',0);await stopping;
  child.stdout.destroy();child.stderr.destroy();child.stdin.destroy();
});

test('legacy service project roots are hydrated from matching per-project state', async t => {
  const root=await directory(t), requests=[];
  const server=http.createServer((request,response)=>{
    requests.push(request.url); response.setHeader('Content-Type','application/json');
    if(request.url==='/api/projects'){response.writeHead(404);response.end('{"error":"old service"}');}
    else if(request.url==='/api/state')response.end(JSON.stringify({projects:[{name:'old project',theme:'gray'}],leaders:[],members:[]}));
    else if(request.url==='/api/state?project=old%20project')response.end(JSON.stringify({project:{name:'old project',root,theme:'gray'}}));
    else{response.writeHead(404);response.end('{}');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve)); t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}`; await fs.writeFile(path.join(root,'web.json'),JSON.stringify({url}));
  const backend=new core.Backend({home:root},{spawn:()=>{throw new Error('must not spawn');}});
  assert.deepEqual(await backend.start(),{url,owned:false});
  assert.deepEqual(await core.projects(url),[{name:'old project',root,theme:'gray'}]);
  assert(requests.includes('/api/state?project=old%20project')); await backend.stop();
});

test('owned Python diagnostics survive exit in a bounded local log', async t => {
  const root=await directory(t), service=await fixture(t), backendDir=path.join(root,'backend');
  await fs.mkdir(backendDir);
  await fs.writeFile(path.join(backendDir,'crew_desktop.py'), 'import sys\nprint('+JSON.stringify(service.url)+', flush=True)\nprint("diagnostic marker", file=sys.stderr, flush=True)\nsys.stdin.readline()\n');
  const backend=new core.Backend({home:path.join(root,'home'),backendDir,python:'python',port:0,isolated:true,smoke:true});
  t.after(()=>backend.stop()); await backend.start(); await backend.stop();
  const text=await fs.readFile(backend.logPath,'utf8'); assert(text.includes('diagnostic marker')); assert(text.length<6200);
  assert.equal(backend.child.exitCode,0);
});
test('portable backend is retained by content identity after extraction disappears', async t => {
  const root = await directory(t), source = path.join(root, 'extracted'), home = path.join(root, 'home');
  await fs.mkdir(source); await fs.writeFile(path.join(source, 'crew_desktop.py'), '# backend v1');
  const first = await core.materializeBackend(source, home);
  await fs.unlink(path.join(first, '.ready')); await fs.writeFile(path.join(first, 'crew_desktop.py'), '# interrupted copy');
  assert.equal(await core.materializeBackend(source, home), first);
  assert.equal(await fs.readFile(path.join(first, 'crew_desktop.py'), 'utf8'), '# backend v1');
  assert.equal(await core.materializeBackend(source, home), first);
  await fs.writeFile(path.join(source, 'crew_desktop.py'), '# backend v2');
  const second = await core.materializeBackend(source, home); assert.notEqual(first, second);
  await fs.rm(source, { recursive: true });
  assert.equal(await fs.readFile(path.join(first, 'crew_desktop.py'), 'utf8'), '# backend v1');
  assert.equal(await fs.readFile(path.join(second, 'crew_desktop.py'), 'utf8'), '# backend v2');
});
test('smoke refuses an existing production registry without making a network call', async t => {
  const root = await directory(t); await fs.writeFile(path.join(root, 'web.json'), '{"url":"http://127.0.0.1:47811"}');
  const backend = new core.Backend({ home: root, smoke: true }, { spawn: () => { throw new Error('must not spawn'); } });
  await assert.rejects(backend.start(), /正式端口/);
});
test('isolated Python service binds port zero and exits gracefully without stale registry', { timeout: 30000 }, async t => {
  const root = await directory(t);
  const backend = new core.Backend({ home: root, backendDir: path.resolve(__dirname, '../../plugin'), python: 'python', port: 0, isolated: true, smoke: true });
  t.after(() => backend.stop());
  const result = await backend.start(); assert.equal(result.owned, true); assert.notEqual(new URL(result.url).port, '0'); assert.notEqual(new URL(result.url).port, '47811');
  assert.deepEqual(await core.projects(result.url), []);
  const repo = await core.newWorkspace(root, 'isolated-project', result.url); assert.equal(repo.name, 'isolated-project');
  await backend.stop(); assert.equal(backend.child.exitCode, 0, backend.errors);
  await assert.rejects(fs.access(path.join(root, 'web.json')));
});
test('simultaneous UI profiles share one OS-locked service and the loser only borrows', {timeout:45000},async t=>{
  const root=await directory(t), config={home:root,backendDir:path.resolve(__dirname,'../../plugin'),python:'python',port:0,isolated:true,smoke:true};
  const first=new core.Backend(config),second=new core.Backend(config);t.after(()=>Promise.all([first.stop(),second.stop()]));
  const results=await Promise.all([first.start(),second.start()]);
  assert.equal(results.filter(result=>result.owned).length,1);assert.equal(results[0].url,results[1].url);
  const owner=first.owned?first:second,borrower=first.owned?second:first;
  assert.equal(borrower.child.exitCode,75);assert.equal(borrower.logPath,undefined);
  await borrower.stop();assert.deepEqual(await core.projects(owner.url),[]);
  assert((await fs.readFile(owner.logPath,'utf8')).startsWith('Crew service started '));
  await owner.stop();assert.equal(owner.child.exitCode,0);
});
