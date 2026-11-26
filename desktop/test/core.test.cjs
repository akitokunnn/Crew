'use strict';
const test = require('node:test'); const assert = require('node:assert/strict');
const fs = require('node:fs/promises'); const path = require('node:path'); const os = require('node:os');
const http = require('node:http'); const { execFile } = require('node:child_process'); const { promisify } = require('node:util');
const core = require('../src/core.cjs'); const run = promisify(execFile);
// The interpreter Crew itself picks on this platform: python3 on macOS, python elsewhere.
const PYTHON = require('../src/python.cjs').defaultPython();
const {listen}=require('./fixtures/loopback.cjs');
async function directory(t) { const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-desktop-test-')); t.after(() => fs.rm(root, { recursive: true, force: true })); return root; }
async function fixture(t, known = []) {
  const calls = []; const server = http.createServer(async (request, response) => {
    calls.push(request.url); response.setHeader('Content-Type', 'application/json');
    if (request.url.split('?')[0] === '/api/op/workspace.list') { response.end(JSON.stringify({ projects: known })); return; }
    response.writeHead(404); response.end(JSON.stringify({ error: 'missing' }));
  });
  await listen(server);
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { url: `http://127.0.0.1:${server.address().port}`, known, calls };
}
// A recording HTTP host: each route answers with a fixed status and JSON body.
async function host(t, routes) {
  const requests = []; const server = http.createServer(async (request, response) => {
    let text = ''; for await (const part of request) text += part;
    requests.push({ method:request.method, url:request.url, headers:request.headers, body:text ? JSON.parse(text) : null });
    const [status, body] = routes[request.url.split('?')[0]] || [404, { error:'missing' }];
    response.writeHead(status, { 'Content-Type':'application/json' }); response.end(JSON.stringify(typeof body === 'function' ? body(request) : body));
  });
  await listen(server);
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { url:`http://127.0.0.1:${server.address().port}`, requests };
}
const TOKEN = 'a'.repeat(43);

test('service URLs reject remote hosts, credentials, redirects paths and untrusted protocols', () => {
  assert.equal(core.loopback('http://127.0.0.1:1234/'), 'http://127.0.0.1:1234');
  for (const value of ['https://example.com/', 'http://example.com:1234/', 'file:///x', 'http://user@127.0.0.1:1234/', 'http://127.0.0.1:1234/api/op/workspace.state', 'http://127.0.0.1:1234/?x=1']) assert.throws(() => core.loopback(value));
});

test('native model discovery can wait beyond the normal API deadline', async t => {
  const server = http.createServer((request, response) => {
    setTimeout(() => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({runtimes:[{id:'detected-native'}]})); }, 5200);
  });
  await listen(server);
  t.after(() => new Promise(resolve => server.close(resolve)));
  const result = await core.request(`http://127.0.0.1:${server.address().port}`, '/api/op/runtimes.list?refresh=1', undefined, 45000);
  assert.equal(result.runtimes[0].id, 'detected-native');
});
test('a caller can abort a read-only request without waiting for its HTTP deadline', async t => {
  const server=http.createServer((request,response)=>{response.setHeader('Content-Type','application/json');response.write('{');});
  await listen(server);
  t.after(()=>{server.closeAllConnections();return new Promise(resolve=>server.close(resolve));});
  const controller=new AbortController(),at=performance.now();
  const pending=core.request(`http://127.0.0.1:${server.address().port}`,'/api/admin/health',undefined,5000,controller.signal);
  setTimeout(()=>controller.abort(),25);
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert(performance.now()-at<1000,'An explicit cancellation must not consume the five-second HTTP deadline');
});
test('the original HTTP deadline still applies when an un-aborted caller signal is supplied', async t => {
  const server=http.createServer((_request,response)=>{response.setHeader('Content-Type','application/json');response.write('{');});
  await listen(server);
  t.after(()=>{server.closeAllConnections();return new Promise(resolve=>server.close(resolve));});
  const base=`http://127.0.0.1:${server.address().port}`;
  for(const signal of [undefined,new AbortController().signal])await assert.rejects(core.request(base,'/api/admin/health',undefined,30,signal),error=>error.name==='TimeoutError');
});
test('smoke requires explicit isolated home and cannot bind the production port', () => {
  assert.throws(() => core.options(['--smoke-test']));
  assert.throws(() => core.options(['--smoke-test', '--home', 'test', '--smoke-output', 'test.json', '--port', '47811']));
  assert.throws(() => core.options(['--port', '-1']));
  assert.equal(core.options(['--home', 'test', '--port', '0']).port, 0);
});
test('an explicit HOME starts locally instead of retaining the saved existing service URL',async t=>{
  const root=await directory(t),homeA=path.join(root,'home A'),homeB=path.join(root,'home B'),file=path.join(root,'launch-settings.json');
  await fs.writeFile(file,JSON.stringify({mode:'existing',home:homeA,source:'bundled',backend_directory:root,service_url:'http://127.0.0.1:12345',port:0,python:'stored-python'}));
  const local=core.options(['--home',homeB],{launchSettingsPath:file});
  assert.equal(local.home,homeB);assert.equal(local.serviceUrl,undefined);assert.equal(local.userData,path.join(homeB,'desktop','ui'));
  assert.equal(core.options(['--python','new-python'],{launchSettingsPath:file}).serviceUrl,'http://127.0.0.1:12345');
  assert.equal(core.options(['--home',homeB,'--service-url','http://127.0.0.1:12346'],{launchSettingsPath:file}).serviceUrl,'http://127.0.0.1:12346');
});
test('an explicit backend or local port clears a saved existing service unless a new URL is explicit',async t=>{
  const root=await directory(t),file=path.join(root,'launch-settings.json'),source=path.join(root,'selected backend');
  await fs.writeFile(file,JSON.stringify({mode:'existing',home:root,source:'bundled',backend_directory:root,service_url:'http://127.0.0.1:12345',port:0,python:'stored-python'}));
  const local=core.options(['--backend-dir',source],{launchSettingsPath:file});
  assert.equal(local.backendDir,source);assert.equal(local.backendSource,'custom');assert.equal(local.serviceUrl,undefined);
  assert.equal(core.options(['--port','0'],{launchSettingsPath:file}).serviceUrl,undefined);
});

test('production, custom data and isolated windows have distinct visible identities', () => {
  assert.equal(core.options([]).instanceName, 'Crew');
  assert.equal(core.options(['--home', path.join(os.tmpdir(), 'crew-independent')]).instanceName, 'Crew · 独立数据');
  assert.equal(core.options(['--home', path.join(os.tmpdir(), 'crew-test'), '--isolated-sources']).instanceName, 'Crew · 隔离测试');
  assert.equal(core.options(['--home', path.join(os.tmpdir(), 'crew-smoke'), '--smoke-test', '--smoke-output', 'evidence.json']).instanceName, 'Crew · 隔离测试');
});
test('new workspace name is a filename rather than an arbitrary path', () => {
  for (const name of ['../escape', 'one/two', 'one\\two', '.', '..', 'CON', 'LPT1.txt', 'name.', '', ' x ']) assert.throws(() => core.workspaceName(name));
  assert.equal(core.workspaceName('我的新项目'), '我的新项目');
});
test('LAN host addresses accept only private IPv4 hosts with a port and no path', () => {
  assert.equal(core.lanAddress('192.168.1.20:47812'), 'http://192.168.1.20:47812');
  assert.equal(core.lanAddress(' http://10.0.0.5:47812/ '), 'http://10.0.0.5:47812');
  for (const value of ['172.16.0.1:1', '172.31.255.254:1', '127.0.0.1:47812']) assert.equal(core.lanAddress(value), 'http://' + value);
  for (const value of ['', '192.168.1.20', 'https://192.168.1.20:47812', 'http://8.8.8.8:47812', '172.32.0.1:47812', '192.169.1.1:47812',
    'localhost:47812', 'crew.local:47812', 'http://user@192.168.1.20:47812', '192.168.1.20:47812/api', '192.168.1.20:47812?x=1', '[::1]:47812', 'not a host'])
    assert.throws(() => core.lanAddress(value), /主机地址/, value);
});
test('the device token is sent only to the authorized LAN host and a 401 reports the lost authorization', async t => {
  const lan = await host(t, { '/api/op/workspace.state':[200, { ok:true }], '/api/op/workspace.list':[401, { error:'设备授权已失效，请在主机上重新生成配对码' }] });
  const local = await host(t, { '/api/op/workspace.state':[200, { ok:true }] });
  const other = await host(t, { '/api/op/workspace.state':[200, { ok:true }] });
  let expired = 0; core.authorizeRemote({ origin:lan.url, token:TOKEN, onUnauthorized:() => { expired++; } });
  t.after(() => core.authorizeRemote(null));
  await core.request(lan.url, '/api/op/workspace.state'); await core.request(local.url, '/api/op/workspace.state');
  assert.equal(lan.requests[0].headers.authorization, 'Bearer ' + TOKEN);
  assert.equal(local.requests[0].headers.authorization, undefined, 'a loopback service never receives the LAN token');
  await assert.rejects(core.request(lan.url, '/api/op/workspace.list'), error => error.status === 401 && /重新生成配对码/.test(error.message));
  assert.equal(expired, 1);
  assert.throws(() => core.serviceOrigin('http://192.168.1.21:47812'), /loopback/);
  core.authorizeRemote(null);
  await core.request(other.url, '/api/op/workspace.state'); assert.equal(other.requests[0].headers.authorization, undefined);
  assert.throws(() => core.authorizeRemote({ origin:lan.url, token:'short', onUnauthorized() {} }), TypeError);
});
test('workspace folders are checked, resolved and created by the backend for both local and LAN connections', async t => {
  const project = { name:'demo', root:'D:\\demo', directory_state:{ status:'ready' } };
  const service = await host(t, { '/api/op/workspace.add_existing':[200, project], '/api/op/workspace.create_folder':[400, { error:'[WinError 183] 当文件已存在时，无法创建该文件。' }],
    '/api/op/workspace.host_folders':[200, { path:'D:\\', parent:null, home:'C:\\Users\\x', roots:['C:\\', 'D:\\'], entries:[], truncated:false }] });
  assert.deepEqual(await core.existingWorkspace('D:\\demo\\src', service.url), project);
  assert.deepEqual(await core.existingWorkspace('D:\\demo', service.url, { newProject:true }), project);
  await assert.rejects(core.newWorkspace('D:\\', 'demo', service.url), /当文件已存在时/);
  assert.equal((await core.hostFolders(service.url, 'D:\\')).path, 'D:\\');
  assert.deepEqual(service.requests.map(({ method, url, body }) => ({ method, url, body })), [
    { method:'POST', url:'/api/op/workspace.add_existing', body:{ directory:'D:\\demo\\src', new_project:false } },
    { method:'POST', url:'/api/op/workspace.add_existing', body:{ directory:'D:\\demo', new_project:true } },
    { method:'POST', url:'/api/op/workspace.create_folder', body:{ parent:'D:\\', name:'demo' } },
    { method:'GET', url:'/api/op/workspace.host_folders?path=D%3A%5C', body:null }]);
});
test('the workspace list must carry the backend directory state for every writable workspace', async t => {
  const ready = { name:'demo', root:'C:\\demo', directory_state:{ status:'missing' } }, history = { name:'storage:w1', root:'已保存的工作区历史', read_only:true };
  assert.deepEqual(await core.projects((await fixture(t, [ready, history])).url), [ready, history]);
  for (const item of [{ name:'demo', root:'C:\\demo' }, { name:'demo', root:'C:\\demo', directory_state:{ status:'unknown' } }, { name:'demo', directory_state:{ status:'ready' } }])
    await assert.rejects(core.projects((await fixture(t, [item])).url), /有效的 Crew 工作区列表/);
});
test('a host download streams into the chosen file and a failed download leaves no file behind', async t => {
  const root = await directory(t), destination = path.join(root, '导出.crewpack');
  const server = http.createServer((request, response) => {
    if (request.url === '/api/op/data.download?id=ok') { assert.equal(request.headers.authorization, 'Bearer ' + TOKEN); response.writeHead(200, { 'Content-Type':'application/zip' }); response.end(Buffer.alloc(300000, 7)); }
    else { response.writeHead(400, { 'Content-Type':'application/json' }); response.end(JSON.stringify({ error:'导出文件已失效，请重新导出' })); }
  });
  await listen(server); t.after(() => new Promise(resolve => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}`;
  core.authorizeRemote({ origin:url, token:TOKEN, onUnauthorized() {} }); t.after(() => core.authorizeRemote(null));
  assert.deepEqual(await core.download(url, '/api/op/data.download?id=ok', destination), { path:destination, bytes:300000 });
  assert.deepEqual(await fs.readFile(destination), Buffer.alloc(300000, 7));
  const failed = path.join(root, 'failed.crewpack');
  await assert.rejects(core.download(url, '/api/op/data.download?id=gone', failed), /导出文件已失效/);
  assert.deepEqual((await fs.readdir(root)).sort(), ['导出.crewpack']);
});
test('a large host package survives the host closing its connection, and a cut-off body is refused', async t => {
  // Like the Python host: headers with Content-Length, the body in 1 MiB writes, then the socket closes.
  const root = await directory(t), net = require('node:net'), size = 6 * 1024 * 1024;
  const server = net.createServer(socket => {
    socket.once('data', request => {
      const full = request.toString().startsWith('GET /api/op/data.download?id=full ');
      socket.write('HTTP/1.0 200 OK\r\nContent-Type: application/zip\r\nContent-Length: ' + size + '\r\nConnection: close\r\n\r\n');
      for (let index = 0; index < (full ? 6 : 2); index++) socket.write(Buffer.alloc(1024 * 1024, index));
      socket.end();
    });
  });
  await listen(server); t.after(() => new Promise(resolve => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}`;
  core.authorizeRemote({ origin:url, token:TOKEN, onUnauthorized() {} }); t.after(() => core.authorizeRemote(null));
  const destination = path.join(root, 'large.crewpack');
  assert.equal((await core.download(url, '/api/op/data.download?id=full', destination)).bytes, size);
  assert.equal((await fs.readFile(destination)).subarray(5 * 1024 * 1024)[0], 5);
  await assert.rejects(core.download(url, '/api/op/data.download?id=cut', path.join(root, 'cut.crewpack')), /主机在下载完成前断开了连接/);
  assert.deepEqual(await fs.readdir(root), ['large.crewpack']);
});
test('desktop pairing asks for a device token without a cookie or page origin', async t => {
  const service = await host(t, { '/remote/login':[200, { ok:true, token:TOKEN, device_id:'device-1' }] });
  assert.deepEqual(await core.pairDesktop(service.url.replace('http://', ''), { code:'12345678', device_name:'书房电脑' }), { url:service.url, device_id:'device-1', token:TOKEN });
  const [login] = service.requests;
  assert.deepEqual(login.body, { code:'12345678', device_name:'书房电脑', kind:'desktop' });
  assert.equal(login.headers['x-crew'], '1'); assert.equal(login.headers.origin, undefined); assert.equal(login.headers.cookie, undefined);
  const refused = await host(t, { '/remote/login':[400, { error:'配对码无效或已过期，请在电脑上重新生成' }] });
  await assert.rejects(core.pairDesktop(refused.url, { code:'00000000', device_name:'x' }), /配对码无效或已过期/);
  const invalid = await host(t, { '/remote/login':[200, { ok:true, token:'short', device_id:'device-1' }] });
  await assert.rejects(core.pairDesktop(invalid.url, { code:'12345678', device_name:'x' }), /配对结果无效/);
});
test('a saved LAN host connects only as a desktop device, and an expired token reports the lost authorization', async t => {
  const health = { ok:true, service:'crew', version:'0.3.17', home:'/Users/host/.crew', instance_id:'host-instance', host_name:'书房的 Mac', pid:9, warnings:[] };
  const good = await host(t, { '/api/access/context':[200, { client:'desktop', remote:true }], '/api/admin/health':[200, health] });
  const credentials = url => ({ credential:async id => ({ id, name:'书房主机', url, token:TOKEN }) });
  t.after(() => core.authorizeRemote(null));
  const backend = new core.RemoteBackend('saved-1', { credentials:credentials(good.url), onUnauthorized() {} });
  assert.deepEqual(await backend.start(), { url:good.url, owned:false });
  assert.equal(backend.name, '书房主机'); assert.equal(backend.health.home, '/Users/host/.crew'); assert.equal(backend.owned, false);
  assert(good.requests.every(request => request.headers.authorization === 'Bearer ' + TOKEN));
  const browser = await host(t, { '/api/access/context':[200, { client:'browser', remote:true }] });
  await assert.rejects(new core.RemoteBackend('saved-2', { credentials:credentials(browser.url), onUnauthorized() {} }).start(), /重新配对/);
  let expired = 0; const revoked = await host(t, { '/api/access/context':[401, { error:'设备授权已失效，请在主机上重新生成配对码' }] });
  await assert.rejects(new core.RemoteBackend('saved-3', { credentials:credentials(revoked.url), onUnauthorized:() => { expired++; } }).start(), /重新生成配对码/);
  assert.equal(expired, 1);
});
test('saved LAN launch settings select the host, while explicit local launch arguments start this computer', async t => {
  const root = await directory(t), file = path.join(root, 'launch-settings.json');
  await fs.writeFile(file, JSON.stringify({ mode:'remote', home:root, source:'bundled', backend_directory:root, service_url:'', port:0, python:'python', remote_backend:'saved-1' }));
  const remote = core.options([], { launchSettingsPath:file });
  assert.equal(remote.remoteBackend, 'saved-1'); assert.equal(remote.serviceUrl, undefined); assert.equal(remote.home, root);
  for (const args of [['--home', root], ['--port', '0'], ['--backend-dir', root], ['--service-url', 'http://127.0.0.1:12345']]) assert.equal(core.options(args, { launchSettingsPath:file }).remoteBackend, undefined, args.join(' '));
  assert.equal(core.options(['--python', 'other-python'], { launchSettingsPath:file }).remoteBackend, 'saved-1');
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
  await listen(invalid); t.after(()=>new Promise(resolve=>invalid.close(resolve)));
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
  await listen(server); t.after(()=>new Promise(resolve=>server.close(resolve)));
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
  const backend=new core.Backend({home:root,backendDir,python:PYTHON,port:0,isolated:true,smoke:true});
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
  const backend=new core.Backend({home:root,backendDir,python:PYTHON,port:0,startupTimeoutMs:25},{spawn:()=>child});
  const began=Date.now();await assert.rejects(backend.start(),/服务启动超时/);
  assert(Date.now()-began<1000,'A hung supervisor shutdown must not block the recovery UI');
  assert.equal(kills,0);assert.equal(written,'');assert.equal(backend.owned,true);
  // Explicit quit still requests shutdown and waits for the real exit.
  const stopping=backend.stop();assert.equal(written,'shutdown\n');
  child.exitCode=0;child.emit('exit',0);await stopping;
  child.stdout.destroy();child.stderr.destroy();child.stdin.destroy();
});

test('owned Python diagnostics survive exit in a bounded local log', async t => {
  const root=await directory(t), service=await fixture(t), backendDir=path.join(root,'backend');
  await fs.mkdir(backendDir);
  await fs.writeFile(path.join(backendDir,'crew_desktop.py'), 'import sys\nprint('+JSON.stringify(service.url)+', flush=True)\nprint("diagnostic marker", file=sys.stderr, flush=True)\nsys.stdin.readline()\n');
  const backend=new core.Backend({home:path.join(root,'home'),backendDir,python:PYTHON,port:0,isolated:true,smoke:true});
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
  const backend = new core.Backend({ home: root, backendDir: path.resolve(__dirname, '../../plugin'), python:PYTHON, port: 0, isolated: true, smoke: true });
  t.after(() => backend.stop());
  const result = await backend.start(); assert.equal(result.owned, true); assert.notEqual(new URL(result.url).port, '0'); assert.notEqual(new URL(result.url).port, '47811');
  assert.deepEqual(await core.projects(result.url), []);
  const repo = await core.newWorkspace(root, 'isolated-project', result.url); assert.equal(repo.name, 'isolated-project');
  await backend.stop(); assert.equal(backend.child.exitCode, 0, backend.errors);
  await assert.rejects(fs.access(path.join(root, 'web.json')));
});
test('simultaneous UI profiles share one OS-locked service and the loser only borrows', {timeout:45000},async t=>{
  const root=await directory(t), config={home:root,backendDir:path.resolve(__dirname,'../../plugin'),python:PYTHON,port:0,isolated:true,smoke:true};
  const first=new core.Backend(config),second=new core.Backend(config);t.after(()=>Promise.all([first.stop(),second.stop()]));
  const results=await Promise.all([first.start(),second.start()]);
  assert.equal(results.filter(result=>result.owned).length,1);assert.equal(results[0].url,results[1].url);
  const owner=first.owned?first:second,borrower=first.owned?second:first;
  assert.equal(borrower.child.exitCode,75);assert.equal(borrower.logPath,undefined);
  await borrower.stop();assert.deepEqual(await core.projects(owner.url),[]);
  assert((await fs.readFile(owner.logPath,'utf8')).startsWith('Crew service started '));
  await owner.stop();assert.equal(owner.child.exitCode,0);
});
