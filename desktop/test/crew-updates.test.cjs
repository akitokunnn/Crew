'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const { CrewUpdates } = require('../src/crew-updates.cjs');

function fixture(t, options = {}) {
  const config = { home: options.home || path.resolve('fixture-home'), backendDir: path.resolve('fixture-backend'), python: 'fixture-python', desktopVersion: options.desktopVersion || '0.1.2' };
  const calls = [], phases = [];
  let restarted = false, switched = false, releases = 0;
  const target = { root: config.backendDir, version: '0.1.2', digest: 'production-digest', generation: options.generation ?? 2 };
  const health = () => ({ ok: true, service: 'crew', home: config.home, pid: restarted ? 202 : 101,
    instance_id: restarted ? 'new-instance' : 'old-instance', version: restarted && options.wrongRestart ? '0.1.1' : '0.1.2',
    plugin_root: config.backendDir, port: 51234 });
  const backend = { owned: options.borrowed !== true, url: 'http://127.0.0.1:51234', config,
    child: { pid: 100 },
    async stop() { calls.push('stop'); },
    async start() { calls.push('start'); restarted = true; return { url: this.url, owned: true }; }
  };
  const manager = new CrewUpdates(config, backend, {
    onChange: state => phases.push(state.phase),
    checkLatest:async()=>{if(options.onlineError)throw new Error(options.onlineError);return {release:null,main:{version:'0.2.5',sha:'a'.repeat(40),url:'https://github.com/akitokunnn/Crew/tree/'+ 'a'.repeat(40)},checked_at:'2026-10-25T09:00:00Z'};},
    stopReadOnly:options.stopReadOnly,
    onRestart: async value => { calls.push('reconnect'); assert.equal(value.instance_id, 'new-instance'); },
    acquireLock: async () => {
      calls.push('lock');
      if (options.lockWait) await options.lockWait;
      return { assert() {}, async release() { calls.push('unlock'); releases++; } };
    },
    request: async (_base, route, body, timeout) => {
      if (route === '/api/admin/health') { calls.push('health'); return health(); }
      if (route === '/api/admin/update/prepare') {
        calls.push('prepare'); assert.deepEqual(body, { instance_id: 'old-instance' }); assert.equal(timeout, 15000);
        if (options.onPrepare) options.onPrepare();
        if (options.prepareWait) await options.prepareWait;
        return options.busy ? { ok: false, status: 'busy', reasons: ['leader 正在回复'] } :
          { ok: true, status: 'ready', instance_id: 'old-instance', pid: 101, gate_id: 'update-gate', managed_pids: [103] };
      }
      if (route === '/api/admin/update/cancel') {
        calls.push('cancel-gate'); assert.deepEqual(body, { instance_id: 'old-instance', gate_id: 'update-gate' }); return { ok: true };
      }
      throw new Error('Unexpected request ' + route);
    },
    run: async (exe, args, runOptions) => {
      assert.equal(exe, 'fixture-python'); assert.equal(runOptions.windowsHide, true);
      assert.equal(args[0], path.join(config.backendDir, 'crew_update.py'));
      assert.equal(args[1], '--home'); assert.equal(args[2], config.home);
      assert.equal(runOptions.env.CREW_HOME,config.home);
      const operation = args[3]; calls.push(operation);
      if (options.onOperation) await options.onOperation(operation);
      if (options.fail === operation) return { stdout: JSON.stringify({ ok: false, operation, error: options.failure || 'fixture ' + operation + ' failed' }) };
      let value = {};
      if (operation === 'inspect') {
        value = { target, cli: { plugin_root: switched ? config.backendDir : path.resolve('old-backend'), version: switched ? '0.1.2' : '0.1.1' },
          plugin: options.pluginBlocked ? { state: 'blocked', reason: '插件实际来源与桌面不同' } :
            { state: 'session-scoped', registered_version: '0.1.2' } };
      } else if (operation === 'backup') value = { path: path.join(config.home, 'backups', 'update', 'crew.db'), integrity: 'ok', bytes: 8192 };
      else if (operation === 'plugin-update') throw new Error('V2 must never update the shared V1 plugin');
      else if (operation === 'stopped') {
        assert.deepEqual(args.slice(4), ['--port', '51234', '--pid', '101', '--pid', '100', '--pid', '103', '--timeout', '20']);
      } else if (operation === 'switch') switched = true;
      else throw new Error('Unexpected helper ' + operation);
      return { stdout: JSON.stringify({ ok: true, operation, ...value }) };
    }
  });
  t.after(() => manager.close());
  return { manager, backend, calls, phases, get releases() { return releases; } };
}

test('owned update backs up before changing installation, keeps the port, verifies and reconnects', async t => {
  const f = fixture(t);
  const result = await f.manager.update();
  assert.equal(result.phase, 'done'); assert.equal(result.error, null); assert.equal(result.queued, false);
  assert.equal(result.backend.instance_id, 'new-instance'); assert.equal(result.backend.port, 51234);
  assert.equal(f.backend.config.port, 51234); assert.equal(result.backup.integrity, 'ok');
  assert.deepEqual(f.calls, ['lock', 'inspect', 'health', 'prepare', 'backup', 'inspect', 'health', 'stop', 'stopped', 'switch', 'start', 'health', 'reconnect', 'inspect', 'unlock']);
  assert.equal(f.releases, 1); assert.equal(f.calls.includes('cancel-gate'), false);
});

test('busy updates queue without backup or stop, release the lock, and allow cancellation', async t => {
  const f = fixture(t, { busy: true });
  const queued = await f.manager.update();
  assert.equal(queued.phase, 'queued'); assert.equal(queued.queued, true); assert.match(queued.message, /leader/);
  assert.equal(f.releases, 1); assert.equal(f.calls.includes('backup'), false); assert.equal(f.calls.includes('stop'), false);
  const cancelled = f.manager.cancel();
  assert.equal(cancelled.phase, 'idle'); assert.equal(cancelled.queued, false); assert.equal(f.manager.timer, null);
});

test('borrowed backends are blocked before prepare or any installation change', async t => {
  const f = fixture(t, { borrowed: true });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /其他窗口/);
  assert.deepEqual(f.calls, ['lock', 'inspect', 'health', 'unlock']);
});

test('backup failure reopens the old gate and leaves the backend and installation alone', async t => {
  const f = fixture(t, { fail: 'backup' });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /backup failed/);
  assert.equal(f.calls.includes('stop'), false); assert.equal(f.calls.includes('switch'), false);
  assert.deepEqual(f.calls.slice(-2), ['cancel-gate', 'unlock']);
});

test('stop timeout keeps the backup and never switches or starts a replacement backend', async t => {
  const f = fixture(t, { fail: 'stopped', failure: '停止超时：后台锁仍被占用' });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /停止超时/); assert.equal(result.backup.integrity, 'ok');
  assert.equal(f.calls.includes('stop'), true); assert.equal(f.calls.includes('switch'), false); assert.equal(f.calls.includes('start'), false);
  assert.equal(f.releases, 1);
});

test('a mismatched actual plugin source blocks update before closing the gate', async t => {
  const f = fixture(t, { pluginBlocked: true });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /共享的 V1 插件/);
  assert.equal(f.calls.includes('prepare'), false); assert.equal(f.calls.includes('backup'), false); assert.equal(f.releases, 1);
});

test('V1 source is rejected before preparing, backing up or stopping the current backend', async t => {
  const f = fixture(t, { generation:1 });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /不是 Crew V2/);
  assert.deepEqual(f.calls,['lock','inspect','health','unlock']);
});

test('the canonical V1 HOME is rejected before starting a helper or taking a lock', async t => {
  const f = fixture(t,{home:path.join(os.homedir(),'.crew')});
  const result = await f.manager.update();
  assert.equal(result.phase,'error'); assert.match(result.error,/V1.*数据目录/);
  assert.deepEqual(f.calls,[]);
});

test('restart verification failure is never reported as a successful update or reconnected', async t => {
  const f = fixture(t, { wrongRestart: true });
  const result = await f.manager.update();
  assert.equal(result.phase, 'error'); assert.match(result.error, /验证未通过/); assert.equal(result.backup.integrity, 'ok');
  assert.equal(f.calls.includes('start'), true); assert.equal(f.calls.includes('reconnect'), false); assert.equal(f.phases.includes('done'), false);
  assert.equal(f.releases, 1);
});

test('repeated update clicks share the in-flight transaction and do not acquire another lock', async t => {
  let unlock;
  const lockWait = new Promise(resolve => { unlock = resolve; });
  const f = fixture(t, { lockWait });
  const first = f.manager.update(), second = f.manager.update();
  assert.equal(first, second); unlock();
  assert.equal((await first).phase, 'done'); assert.equal(f.calls.filter(call => call === 'lock').length, 1);
});

for (const busy of [true, false]) {
  test('cancellation while prepare is pending stays cancelled after a ' + (busy ? 'busy' : 'ready') + ' result', async t => {
    let finishPrepare, notifyPrepared;
    const prepareWait = new Promise(resolve => { finishPrepare = resolve; });
    const prepared = new Promise(resolve => { notifyPrepared = resolve; });
    const f = fixture(t, { busy, prepareWait, onPrepare: notifyPrepared });
    const pending = f.manager.update();
    await prepared;
    f.manager.cancel(); finishPrepare();
    const result = await pending;
    assert.equal(result.phase, 'idle'); assert.equal(result.queued, false); assert.equal(f.manager.timer, null);
    assert.equal(f.calls.includes('backup'), false); assert.equal(f.calls.includes('stop'), false);
    assert.equal(f.calls.filter(call => call === 'cancel-gate').length, busy ? 0 : 1);
    assert.equal(f.releases, 1);
  });
}


test('online main and release are separate from the local target and a newer local build is labelled ahead',async t=>{
  const f=fixture(t,{desktopVersion:'0.2.6'});
  const state=await f.manager.check();
  assert.equal(state.online.main.version,'0.2.5');assert.equal(state.online.release,null);
  assert.equal(state.online.main_comparison,-1);assert.equal(state.target.version,'0.1.2');
  assert.equal(state.online_error,null);assert.equal(state.can_update,true);
});

test('online failure stays visible without replacing latest with a local version or disabling local sync',async t=>{
  const f=fixture(t,{onlineError:'GitHub rate limited'});
  const state=await f.manager.check();
  assert.equal(state.online,null);assert.match(state.online_error,/rate limited/);
  assert.equal(state.can_update,true);assert.equal(state.phase,'idle');assert.equal(state.online_checking,false);
});

test('quit waits for an active HOME switch and its complete update transaction without publishing to a closed shell',async t=>{
  let started,release;
  const running=new Promise(resolve=>{started=resolve;}),gate=new Promise(resolve=>{release=resolve;});
  let stops=0;
  const f=fixture(t,{stopReadOnly:async()=>{stops++;},onOperation:async operation=>{if(operation==='switch'){started();await gate;}}});
  const updating=f.manager.update();await running;
  const before=f.phases.length,time=performance.now();let closed=false;
  const closing=f.manager.close().then(()=>{closed=true;});
  await new Promise(resolve=>setTimeout(resolve,70));assert.equal(closed,false);
  release();await Promise.all([updating,closing]);
  assert.equal(f.manager.snapshot().phase,'done');assert.equal(f.releases,1);
  assert.deepEqual(f.calls.slice(-4),['health','reconnect','inspect','unlock']);
  assert.equal(f.phases.length,before);assert.equal(stops,0);
  console.log('active Crew installer preserved for '+(performance.now()-time).toFixed(1)+'ms through completion');
});
