"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/ui-actions.js");

test("features register commands independently and unclaimed commands remain unhandled", () => {
  const actions=create(), calls=[];
  actions.register("workspace", {attention:context=>calls.push(["workspace",context.element.dataset.id])});
  actions.register("compaction", {"context-compact":context=>calls.push(["compact",context.element.dataset.id])});
  assert.equal(actions.dispatch("attention",{element:{dataset:{id:"leader"}}}),true);
  assert.equal(actions.dispatch("context-compact",{element:{dataset:{id:"member"}}}),true);
  assert.equal(actions.dispatch("unclaimed"),false);
  assert.deepEqual(calls,[["workspace","leader"],["compact","member"]]);
});

test("duplicate ownership fails before publishing any part of a feature", () => {
  const actions=create(); actions.register("workspace",{attention:()=>{}});
  assert.throws(()=>actions.register("another",{newAction:()=>{},attention:()=>{}}),/already owned by workspace/);
  assert.equal(actions.dispatch("newAction"),false);
  assert.throws(()=>actions.register("workspace",{newAction:()=>{}}),/owner already registered/);
});

test("unregister is idempotent and a stale disposer cannot remove a remounted feature", () => {
  const actions=create(), calls=[];
  const first=actions.register("workspace",{attention:()=>calls.push("old")}); first();
  actions.register("workspace",{attention:()=>calls.push("new")}); first();
  assert.equal(actions.dispatch("attention"),true); assert.deepEqual(calls,["new"]);
  actions.destroy(); assert.deepEqual(actions.owners(),[]); assert.equal(actions.dispatch("attention"),false);
});

test("delegation ignores disabled controls and passes the actual nested event target's action owner", () => {
  const actions=create(), calls=[];
  actions.register("workspace",{attention:context=>calls.push(context.element)});
  const element={dataset:{act:"attention"},disabled:true}, event={target:{closest:()=>element}};
  assert.equal(actions.handle(event),false); element.disabled=false;
  assert.equal(actions.handle(event),true); assert.equal(calls[0],element);
  assert.equal(actions.handle({target:{closest:()=>null}}),false);
});

test("synchronous and asynchronous failures keep the real error for the application presenter", async () => {
  const failures=[], actions=create({onError:error=>failures.push(error.message)});
  actions.register("feature",{sync:()=>{throw Error("actual sync failure");},async:async()=>{throw Error("actual async failure");}});
  assert.equal(actions.dispatch("sync"),true); assert.equal(actions.dispatch("async"),true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(failures,["actual sync failure","actual async failure"]);
});

test("a departed owner's late failure cannot notify a new instance of that feature", async()=>{
  const failures=[],actions=create({onError:error=>failures.push(error.message)});
  let reject;
  const unregister=actions.register("feature",{save:()=>new Promise((_resolve,rejectRequest)=>reject=rejectRequest)});
  actions.dispatch("save");unregister();actions.register("feature",{save:()=>{}});
  reject(Error("old request failed"));await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(failures,[]);
});
