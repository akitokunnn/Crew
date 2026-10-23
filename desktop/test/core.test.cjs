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
