"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/context-compaction.js");
const {create:createActions} = require("../crew/web/ui-actions.js");

function fixture(runtime="codex", overrides={}) {
  const sessions=new Map(["leader","member"].map(id=>[id,{session_id:id,name:id,runtime,where:"bg"}]));
  const calls=[], notices=[], subscriptions=new Map(); let changed=0, refreshed=0;
  const options={findSession:id=>sessions.get(id),notify:text=>notices.push(text),changed:()=>changed++,refresh:async()=>refreshed++,
    subscribe:async(id,callbacks)=>{const record={...callbacks,closed:0};subscriptions.set(id,record);return()=>record.closed++;},
    request:async id=>{calls.push(id);return{queued:true,message:"本轮结束后压缩"};},...overrides};
  const compaction=create(options);
  return {compaction,options,calls,notices,subscriptions,sessions,changed:()=>changed,refreshed:()=>refreshed};
}

test("immediate confirmation and after-turn gauges share one lifecycle and preserve their request mode", async()=>{
  const requests=[];
  const f=fixture("claude",{request:async(id,options)=>{requests.push([id,options.afterTurn]);return{queued:options.afterTurn};}});
  await f.compaction.compact("leader",{afterTurn:false});
  await f.compaction.compact("leader");
  assert.deepEqual(requests,[["leader",false]]);
  f.subscriptions.get("leader").onFinish({type:"compact"});
  await f.compaction.compact("leader");
  assert.deepEqual(requests,[["leader",false],["leader",true]]);
});

test("CC and Codex suppress duplicate clicks and retain per-session queued state until a real completion", async () => {
  for (const runtime of ["claude","codex"]) {
    let release; const f=fixture(runtime,{request:async id=>{f.calls.push(id);return new Promise(resolve=>release=resolve);}});
    const work=f.compaction.compact("member"); await Promise.resolve();
    assert.equal(f.compaction.phase("member"),"submitting");
    await f.compaction.compact("member"); assert.deepEqual(f.calls,["member"]);
    assert.equal(f.compaction.phase("leader"),null);
    release({queued:true,message:"本轮结束后压缩"}); await work;
    assert.equal(f.compaction.phase("member"),"queued"); assert.equal(f.refreshed(),1);
    f.subscriptions.get("member").onFinish({type:"compact",seq:3});
    assert.equal(f.compaction.phase("member"),null); assert.deepEqual(f.compaction.pendingIds(),[]);
    assert.equal(f.subscriptions.get("member").closed,1); assert(f.notices.some(text=>text.includes("本轮结束后压缩")));
  }
});

test("failure and cancellation restore controls and preserve the actual reason", async () => {
  const failure=fixture("claude",{request:async()=>{throw Error("原生压缩拒绝");}});
  await assert.rejects(failure.compaction.compact("member"),/原生压缩拒绝/);
  assert.equal(failure.compaction.phase("member"),null); assert.equal(failure.subscriptions.get("member").closed,1);
  const cancelled=fixture(); await cancelled.compaction.compact("leader");
  cancelled.subscriptions.get("leader").onFinish({type:"compact_cancelled",reason:"进程已退出"});
  assert.equal(cancelled.compaction.phase("leader"),null); assert(cancelled.notices.some(text=>text.includes("进程已退出")));
});

test("a fast completion cannot be reset by its later HTTP receipt or a stale completion from a previous operation", async () => {
  let f; f=fixture("codex",{request:async()=>{f.subscriptions.get("member").onFinish({type:"compact"});return{queued:true};}});
  await f.compaction.compact("member"); const old=f.subscriptions.get("member");
  assert.equal(f.compaction.phase("member"),null);
  f.options.request=async()=>({queued:true}); await f.compaction.compact("member");
  old.onFinish({type:"compact"}); assert.equal(f.compaction.phase("member"),"queued");
  f.subscriptions.get("member").onFinish({type:"compact"}); assert.equal(f.compaction.phase("member"),null);
});

test("external Claude sessions explain the native command without issuing a request", async () => {
  const f=fixture("claude");f.sessions.get("member").where="vscode";
  await f.compaction.compact("member");assert.equal(f.calls.length,0);assert.equal(f.subscriptions.size,0);
  assert.match(f.notices[0],/member.*原工具.*\/compact/);
});

test("different people can compact independently and an unrelated completion cannot clear another person's state", async () => {
  const f=fixture();await Promise.all([f.compaction.compact("leader"),f.compaction.compact("member")]);
  f.subscriptions.get("leader").onFinish({type:"compact"});
  assert.equal(f.compaction.phase("leader"),null);assert.equal(f.compaction.phase("member"),"queued");
});

test("feature action registration and teardown do not require editing the central click dispatcher", async () => {
  const actions=createActions({onError:error=>{throw error;}}),f=fixture("codex",{actions});
  assert.equal(actions.dispatch("context-compact",{element:{dataset:{id:"member"}}}),true);
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.calls,["member"]);
  f.compaction.destroy();assert.equal(f.subscriptions.get("member").closed,1);
  assert.equal(actions.dispatch("context-compact",{element:{dataset:{id:"member"}}}),false);
  assert.deepEqual(f.compaction.pendingIds(),[]);
});

test("teardown during subscription setup closes its eventual stream and does not send or refresh", async () => {
  let release, closed=0;const f=fixture("codex",{subscribe:()=>new Promise(resolve=>release=resolve)});
  const work=f.compaction.compact("member");f.compaction.destroy();release(()=>closed++);await work;
  assert.equal(closed,1);assert.equal(f.calls.length,0);assert.equal(f.refreshed(),0);
});

test("completion or failure delivered before subscribe resolves never starts another compression", async () => {
  for (const type of ["compact","compact_cancelled","error"]) {
    let closed=0;
    const f=fixture("codex",{subscribe:async(_id,callbacks)=>{callbacks.onFinish({type,reason:"observed terminal result"});return()=>closed++;}});
    await f.compaction.compact("member");
    assert.equal(f.calls.length,0); assert.equal(f.refreshed(),1); assert.equal(closed,1);
    assert.equal(f.compaction.phase("member"),null);
    if (type!=="compact") assert(f.notices.some(text=>text.includes("observed terminal result")));
  }
});
