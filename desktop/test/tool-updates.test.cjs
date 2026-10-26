'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { ToolUpdates } = require('../src/tool-updates.cjs');
const record = id => ({id, installed_version:'1.0.0', latest_version:'1.1.0', installation:'npm',
  update_available:true, can_update:true, error:null, blocked_reason:null});
const checked = () => ({ok:true, operation:'check', checked_at:'2026-10-24T00:00:00Z', runtimes:['claude','codex'].map(record)});
async function fixture(t, handler) {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-tools-test-')), calls = [], states = [];
  const manager = new ToolUpdates({home, backendDir:path.join(home,'backend'), python:'fixture-python'}, {
    onChange:state=>states.push(state), run:async (exe,args,options)=>{
      calls.push({exe,args,options});
      return {stdout:JSON.stringify(await handler(args[3],args[4],calls.length))};
    }
  });
  t.after(async()=>{ await manager.close(); await fs.rm(home,{recursive:true,force:true}); });
  await manager.initialize();
  return {manager,home,calls,states};
}

test('checks deduplicate, display both versions and never auto-update without opting in',async t=>{
  let release; const pending=new Promise(resolve=>{release=resolve;});
  const f=await fixture(t,async operation=>{assert.equal(operation,'check');await pending;return checked();});
  const first=f.manager.check(),second=f.manager.check();assert.equal(first,second);
  assert.equal(f.manager.snapshot().checking,true);release();const state=await first;
  assert.equal(f.calls.length,1);assert.equal(state.autoUpdate,false);assert.equal(state.tools[1].latest_version,'1.1.0');
  assert.equal(f.calls[0].options.windowsHide,true);assert.equal(f.calls[0].options.shell,undefined);
  assert.equal(f.calls[0].options.timeout,45000);
});

test('tool checks use bundled control code when the inactive custom backend is missing',async t=>{
  const f=await fixture(t,async()=>checked()),helper=path.join(f.home,'bundled controls');
  f.manager.config.backendDir=path.join(f.home,'missing custom backend');f.manager.config.helperBackendDir=helper;
  const state=await f.manager.check();assert.equal(state.lastError,null);assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].args[0],path.join(helper,'crew_tool_versions.py'));assert.equal(f.calls[0].options.cwd,helper);
});

test('busy updates remain queued, retry on a later pass and retain settings across restart',async t=>{
  let busy=true;
  const f=await fixture(t,async(operation,id)=> operation==='check'?checked(): busy ?
    {ok:false,operation:'update',runtime:id,status:'blocked',before:record(id),running_count:2,blocked_reason:'有 2 个会话使用此版本'}:
    {ok:true,operation:'update',runtime:id,status:'updated',after:{...record(id),installed_version:'1.1.0',update_available:false}});
  let state=await f.manager.update('codex');assert.equal(state.tools[1].phase,'queued');assert.match(state.tools[1].message,/2/);
  const saved=JSON.parse(await fs.readFile(f.manager.file,'utf8'));assert.deepEqual(saved.queue,[{id:'codex',source:'manual'}]);
  busy=false;state=await f.manager._drain();assert.equal(state.tools[1].phase,'done');assert.equal(state.tools[1].installed_version,'1.1.0');
  assert.deepEqual(JSON.parse(await fs.readFile(f.manager.file,'utf8')).queue,[]);
  await assert.rejects(f.manager.update('shell'),/不支持/);assert.equal(f.calls.length,2);
});

test('cancelling a queued request prevents its later execution',async t=>{
  const f=await fixture(t,async(operation,id)=>({ok:false,operation:'update',runtime:id,status:'blocked',before:record(id),blocked_reason:'正在运行'}));
  await f.manager.update('claude');await f.manager.cancel('claude');await f.manager._drain();
  assert.equal(f.calls.length,1);assert.equal(f.manager.snapshot().tools[0].phase,'idle');
});

test('explicit auto-update opt-in queues available tools, disabling it clears only automatic requests',async t=>{
  const f=await fixture(t,async(operation,id)=>operation==='check'?checked():{ok:false,operation:'update',runtime:id,status:'blocked',before:record(id),blocked_reason:'会话使用中'});
  await f.manager.settings({autoUpdate:true});await f.manager._checking;
  assert.deepEqual(f.manager.snapshot().tools.map(item=>item.phase),['queued','queued']);
  await f.manager.update('codex');await f.manager.settings({autoUpdate:false});
  const state=f.manager.snapshot();assert.equal(state.tools[0].phase,'idle');assert.equal(state.tools[1].phase,'queued');
  assert.deepEqual(JSON.parse(await fs.readFile(f.manager.file,'utf8')).queue,[{id:'codex',source:'manual'}]);
});

test('helper failures are visible and cannot be mistaken for successful updates',async t=>{
  const f=await fixture(t,async()=>{throw new Error('fixture network failure');});
  const checkedState=await f.manager.check();assert.match(checkedState.lastError,/network failure/);
  const updated=await f.manager.update('claude');assert.equal(updated.tools[0].phase,'error');assert.match(updated.tools[0].error,/network failure/);
  assert.deepEqual(JSON.parse(await fs.readFile(f.manager.file,'utf8')).queue,[]);
});

test('application shutdown waits for an installer already running and persists its result',async t=>{
  let release,started;
  const gate=new Promise(resolve=>{release=resolve;}),running=new Promise(resolve=>{started=resolve;});
  const f=await fixture(t,async(operation,id)=>{
    started();await gate;
    return {ok:true,operation:'update',runtime:id,status:'updated',after:{...record(id),installed_version:'1.1.0',update_available:false}};
  });
  const updating=f.manager.update('claude');await running;
  assert.equal(f.calls[0].options.timeout,0,'an active installer must not lose its helper or OS lock to a timeout');
  let closed=false;const closing=f.manager.close().then(()=>{closed=true;});
  const before=f.states.length,time=performance.now();
  await new Promise(resolve=>setTimeout(resolve,70));assert.equal(closed,false);
  release();await Promise.all([updating,closing]);assert.equal(closed,true);
  assert.equal(f.manager.snapshot().tools[0].phase,'done');
  assert.deepEqual(JSON.parse(await fs.readFile(f.manager.file,'utf8')).queue,[]);
  assert.equal(f.states.length,before);
  console.log('active tool installer preserved for '+(performance.now()-time).toFixed(1)+'ms and saved its result');
});

test('a fresh check cannot retain a stale successful-update badge for a different installation',async t=>{
  const f=await fixture(t,async(operation,id)=>operation==='update' ?
    {ok:true,operation:'update',runtime:id,status:'updated',after:{...record(id),installed_version:'1.1.0',update_available:false}} :
    {...checked(),runtimes:[{...record('claude'),installation:'unknown',can_update:false,blocked_reason:'来源未知'},record('codex')]});
  await f.manager.update('claude');assert.equal(f.manager.snapshot().tools[0].phase,'done');
  const state=await f.manager.check();assert.equal(state.tools[0].phase,'idle');assert.equal(state.tools[0].message,null);
  assert.equal(state.tools[0].can_update,false);assert.equal(state.tools[0].blocked_reason,'来源未知');
});

test('turning auto-update off while a busy guard runs cancels its eventual blocked retry',async t=>{
  let started,release;const running=new Promise(resolve=>{started=resolve;}),gate=new Promise(resolve=>{release=resolve;});
  let updates=0;
  const f=await fixture(t,async(operation,id)=>{
    if(operation==='check') return {...checked(),runtimes:['claude','codex'].map(id=>({...record(id),can_update:false,can_queue:true,running_count:2}))};
    updates++;started();await gate;
    return {ok:false,operation:'update',runtime:id,status:'blocked',before:{...record(id),can_update:false,can_queue:true},blocked_reason:'会话使用中'};
  });
  await f.manager.settings({autoUpdate:true});await running;
  const draining=f.manager._draining;await f.manager.settings({autoUpdate:false});release();await draining;
  await f.manager._drain();assert.equal(updates,1);
  assert.equal(f.manager.snapshot().tools[0].phase,'idle');
  assert.deepEqual(JSON.parse(await fs.readFile(f.manager.file,'utf8')).queue,[]);
});
