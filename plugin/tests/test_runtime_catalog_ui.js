"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create,validateSelection}=require("../crew/web/runtime-catalog.js");
const catalog=()=>[{id:"claude",name:"Claude Code",available:true,models:[{id:"sonnet",name:"Sonnet"}],efforts:["low","high"]},{id:"codex",name:"Codex",available:true,models:[{id:"gpt",name:"GPT",efforts:["medium","high"]}],efforts:["medium","high"]}];
test("discovery is explicit, deduplicated and observable without binding to a dialog",async()=>{
  let release,requests=0;const value={catalog:catalog()},module=create({snapshot:()=>value,discover:()=>{requests++;return new Promise(resolve=>release=resolve);}}),states=[];
  const unsubscribe=module.subscribe(()=>states.push(module.status().loading));assert.equal(requests,0);
  const first=module.refresh(),second=module.refresh();await Promise.resolve();assert.equal(requests,1);assert.equal(module.status().loading,true);
  const next=catalog();next[1].models[0].name="New native name";release({runtimes:next});await Promise.all([first,second]);
  assert.equal(module.modelName("gpt","codex"),"New native name");assert.deepEqual(states,[true,false]);unsubscribe();
});
test("synchronous failure and invalid results retain the actual error and permit an explicit retry",async()=>{
  let attempts=0;const value={catalog:catalog()},module=create({snapshot:()=>value,discover:()=>{attempts++;if(attempts===1)throw Error("CLI unavailable");if(attempts===2)return{runtimes:null};return{runtimes:catalog()};}});
  await module.refresh();assert.equal(module.status().error,"CLI unavailable");await module.refresh();assert.match(module.status().error,/格式无效/);
  await module.refresh();assert.equal(attempts,3);assert.equal(module.status().error,null);
});
test("legacy snapshots are adapted without probing an unsupported discovery endpoint",async()=>{
  const module=create({snapshot:()=>({legacyModels:[{id:"old",name:"Native old"}],legacyEfforts:["high"]}),discover:()=>{throw Error("must not probe");}});
  await module.refresh();assert.equal(module.info({}).id,"claude");assert.equal(module.info("codex").available,false);assert.equal(module.modelName("old"),"Native old");assert.equal(module.status().canRefresh,false);
});
test("defaults follow explicit purpose and global settings without silently choosing the first installed carrier",()=>{
  const value={catalog:catalog(),choices:{}},module=create({snapshot:()=>value});assert.equal(module.defaultRuntime(),"");value.choices.default={runtime:"codex"};assert.equal(module.taskRuntime("leader"),"codex");value.choices.leader={runtime:"claude"};assert.equal(module.taskRuntime("leader"),"claude");
  value.choices.leader={runtime:null};assert.equal(module.taskRuntime("leader"),"");value.catalog[0].available=false;assert.equal(module.taskRuntime("leader"),"codex");
});
test("shared member/leader choice validation preserves custom native choices and rejects actual capability conflicts",()=>{
  const choices=catalog(),custom={runtime:"codex",model:"future-native",effort:"deep"};assert.deepEqual(validateSelection(custom,choices),custom);assert.deepEqual(custom,{runtime:"codex",model:"future-native",effort:"deep"});
  assert.throws(()=>validateSelection({runtime:"codex",model:"gpt",effort:"low"},choices),/不支持/);choices[1].available=false;choices[1].error="native sign-in required";assert.throws(()=>validateSelection(custom,choices),/native sign-in required/);
});
test("a destroyed catalog does not publish a late native discovery result",async()=>{
  let release,changed=0;const initial=catalog(),module=create({snapshot:()=>({catalog:initial}),discover:()=>new Promise(resolve=>release=resolve)});module.subscribe(()=>changed++);const pending=module.refresh();await Promise.resolve();module.destroy();release({runtimes:[]});await pending;assert.equal(changed,1);assert.equal(module.list(),initial);
});
module.exports={catalog};
