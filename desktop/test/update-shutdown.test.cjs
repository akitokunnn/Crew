'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { ToolUpdates } = require('../src/tool-updates.cjs');
const { CrewUpdates } = require('../src/crew-updates.cjs');
const { checkLatest } = require('../src/crew-latest.cjs');
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
function deferred() { let resolve,reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no; }); return {promise,resolve,reject}; }
function command() {
  const pending = deferred(), child = new EventEmitter();
  Object.assign(child,{pid:2147483000,exitCode:null,signalCode:null});
  pending.promise.child = child;
  return {...pending,child};
}
const abortError = () => Object.assign(new Error('fixture read cancelled'),{name:'AbortError'});
const record = id => ({id,installed_version:'1.0.0',latest_version:'1.1.0',installation:'npm',update_available:true,can_update:true,error:null});

test('closing tool metadata cancels its owned helper promptly without starting queued installs or publishing errors',async t => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(),'crew-readonly-exit-'));
  const pending = command(), started = deferred(), states = [], operations = [];
  let stopped = 0;
  const manager = new ToolUpdates({home,backendDir:home,python:'fixture'}, {
    onChange:state => states.push(state),
    run:(_exe,args) => { operations.push(args[3]); started.resolve(); return pending.promise; },
    stopReadOnly:async child => { assert.equal(child,pending.child); stopped++; pending.reject(abortError()); }
  });
  t.after(async () => { pending.resolve({stdout:JSON.stringify({ok:true,operation:'check',runtimes:['claude','codex'].map(record)})}); await manager.close(); await fs.rm(home,{recursive:true,force:true}); });
  manager.queue.set('codex','manual');
  const checking = manager.check(); await started.promise;
  const before = states.length, time = performance.now(); let closed = false;
  const closing = manager.close().then(() => { closed=true; });
  try {
    await delay(100);
    assert.equal(closed,true,'read-only check must not make exit wait for the metadata timeout');
    assert.equal(stopped,1); assert(performance.now()-time<1000);
  } finally { pending.resolve({stdout:JSON.stringify({ok:true,operation:'check',runtimes:['claude','codex'].map(record)})}); await Promise.all([checking,closing]); }
  assert.deepEqual(operations,['check']); assert.equal(states.length,before);
  assert.equal(manager.snapshot().lastError,null); assert.equal(manager.queue.get('codex'),'manual');
});

test('closing Crew metadata cancels inspect and online discovery while preserving the last valid state',async () => {
  const pending = command(), started = deferred(), online = deferred(), onlineStarted = deferred(), states = [];
  let stopped=0, locks=0, onlineSignal;
  const home=path.resolve('fixture-readonly-home'), backendDir=path.resolve('fixture-backend');
  const manager=new CrewUpdates({home,backendDir,python:'fixture',desktopVersion:'0.0.1'}, {owned:true,url:'http://127.0.0.1:12345'}, {
    onChange:state => states.push(state),
    run:() => { started.resolve(); return pending.promise; },
    request:async () => { throw Error('health must not start after close'); },
    acquireLock:async () => { locks++; return {assert(){},async release(){}}; },
    stopReadOnly:async child => { assert.equal(child,pending.child); stopped++; pending.reject(abortError()); },
    checkLatest:options => { onlineSignal=options?.signal; onlineSignal?.addEventListener('abort',() => online.reject(abortError()),{once:true}); onlineStarted.resolve(); return online.promise; }
  });
  manager.state.online={main:{version:'0.0.1'}};
  const checking=manager.check(); await Promise.all([started.promise,onlineStarted.promise]);
  const queued=manager.update();
  const before=states.length,time=performance.now(); let closed=false;
  const closing=manager.close().then(() => { closed=true; });
  try {
    await delay(100);
    assert.equal(closed,true); assert.equal(stopped,1); assert.equal(onlineSignal.aborted,true);
    assert(performance.now()-time<1000);
  } finally { pending.resolve({stdout:'{"ok":false,"operation":"inspect","error":"late result"}'}); online.resolve({main:{version:'0.0.1'},release:null}); await Promise.all([checking,queued,closing]); }
  assert.equal(states.length,before); assert.equal(manager.state.error,null); assert.equal(manager.state.online_error,null);
  assert.equal(manager.state.online.main.version,'0.0.1'); assert.equal(manager.state.online_checking,false);
  assert.equal(locks,0,'an update queued behind a cancelled check must never acquire an installation lock');
});

test('Crew pure check aborts an already-running health read without cancelling update transactions',async () => {
  const health=deferred(), started=deferred(); let signal;
  const home=path.resolve('fixture-health-home'), backendDir=path.resolve('fixture-backend');
  const manager=new CrewUpdates({home,backendDir,python:'fixture',desktopVersion:'0.0.1'}, {owned:true,url:'http://127.0.0.1:12345'}, {
    run:async () => ({stdout:JSON.stringify({ok:true,operation:'inspect',target:{root:backendDir,version:'0.0.1'},cli:{plugin_root:backendDir,version:'0.0.1'},plugin:{state:'current'}})}),
    request:(_url,_route,_body,_timeout,externalSignal) => { signal=externalSignal; signal?.addEventListener('abort',() => health.reject(abortError()),{once:true}); started.resolve(); return health.promise; },
    checkLatest:async () => ({main:{version:'0.0.1'},release:null})
  });
  const checking=manager.check(); await started.promise; let closed=false;
  const closing=manager.close().then(() => { closed=true; });
  try { await delay(100); assert.equal(closed,true); assert.equal(signal.aborted,true); }
  finally { health.reject(abortError()); await Promise.all([checking,closing]); }
  assert.equal(manager.state.error,null);
});

test('online version query links external cancellation to fetch and stops before its ten-second deadline',async () => {
  const controller=new AbortController(); let signal;
  const checking=checkLatest({signal:controller.signal,timeoutMs:250,fetchImpl:async (_url,options) => {
    signal=options.signal;
    return new Promise((_resolve,reject) => signal.addEventListener('abort',() => reject(signal.reason),{once:true}));
  }});
  const time=performance.now(); controller.abort(abortError());
  await assert.rejects(checking,error => error.name==='AbortError');
  assert.equal(signal.aborted,true); assert(performance.now()-time<150);
});

test('read-only cancellation reaps its actual helper and descendant process',async t => {
  const { ReadOnlyCommands }=require('../src/readonly-command.cjs');
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'crew-owned-helper-'));
  const pids=path.join(home,'pids.json'), run=promisify(execFile), spawned=deferred();
  const reads=new ReadOnlyCommands({run:(exe,args,options) => { const result=run(exe,args,options); spawned.resolve(result.child); return result; }});
  const script="const fs=require('node:fs'),{spawn}=require('node:child_process');const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{windowsHide:true,stdio:'ignore'});fs.writeFileSync(process.argv[1],JSON.stringify([process.pid,child.pid]));setInterval(()=>{},1000);";
  const running=reads.run(process.execPath,['-e',script,pids],{windowsHide:true,timeout:45000});
  const outcome=running.catch(error => error);
  const child=await spawned.promise;
  t.after(async () => { await reads.close(); await fs.rm(home,{recursive:true,force:true}); });
  let ids;
  for(let attempt=0;attempt<200;attempt++) { try { ids=JSON.parse(await fs.readFile(pids,'utf8')); break; } catch { await delay(10); } }
  assert.equal(ids?.length,2,'fixture parent and descendant must both be running');
  const time=performance.now(); await reads.close(); const elapsed=performance.now()-time;
  assert.equal((await outcome).name,'AbortError'); assert.notEqual(child.exitCode,null);
  for(const pid of ids) assert.throws(() => process.kill(pid,0),/ESRCH|not found/i,'owned process remains alive: '+pid);
  assert(elapsed<3000,'owned helper cancellation took '+elapsed+'ms');
  console.log('owned read-only helper tree cancelled in '+elapsed.toFixed(1)+'ms');
});
