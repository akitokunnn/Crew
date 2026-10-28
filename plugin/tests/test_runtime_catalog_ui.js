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
  value.choices.leader={runtime:null};assert.equal(module.taskRuntime("leader"),"");value.catalog[0].available=false;assert.equal(module.taskRuntime("leader"),"");
});
test("unconfigured leader and member defer selection even with one discovered carrier, preserving explicit values",()=>{
  for(const installed of [[],["claude"],["codex"],["claude","codex"]]){
    const rows=catalog();for(const row of rows)row.available=installed.includes(row.id);
    const choices={},view=create({snapshot:()=>({catalog:rows,choices})});
    for(const role of ["leader","member"])assert.equal(view.taskRuntime(role),"");
    assert.deepEqual(validateSelection({runtime:null,model:null,effort:null},rows),{});
    choices.default={runtime:"claude"};assert.equal(view.taskRuntime("leader"),"claude");
    choices.member={runtime:"codex"};assert.equal(view.taskRuntime("member"),"codex");
  }
});
test("effort support follows native model data, independent of model name, and native default remains selectable",()=>{
  const rows=catalog(),name="claude-haiku-4-5-20251001";
  rows[0].models=[{id:name,efforts:["high"]},{id:"unrelated-name",efforts:[]}];
  const view=create({snapshot:()=>({catalog:rows})}),person={runtime:"claude",model:name,effort:null};
  assert.deepEqual(view.efforts(person,name),["high"]);
  assert.deepEqual(view.optionChange(person,"effort","high"),{effort:"high"});
  assert.deepEqual(view.optionChange(person,"effort",null),{effort:null});
  assert.throws(()=>view.optionChange({...person,model:"unrelated-name"},"effort","high"),/不支持/);
});
test("selection receipt names the backend choice and reason without claiming discovery has loaded a model",()=>{
  const label=require("../crew/web/runtime-catalog.js").selectionResolutionLabel({selected_runtime:"codex",available:["codex"],reason:"未找到首选工具",loaded:"not_confirmed"});
  assert.match(label,/Codex/);assert.match(label,/未找到首选工具/);assert.match(label,/未确认.*加载/);
});
test("shared member/leader choice validation preserves custom native choices and rejects actual capability conflicts",()=>{
  const choices=catalog(),custom={runtime:"codex",model:"future-native",effort:"deep"};assert.deepEqual(validateSelection(custom,choices),custom);assert.deepEqual(custom,{runtime:"codex",model:"future-native",effort:"deep"});
  assert.throws(()=>validateSelection({runtime:"codex",model:"gpt",effort:"low"},choices),/不支持/);choices[1].available=false;choices[1].error="native sign-in required";assert.throws(()=>validateSelection(custom,choices),/native sign-in required/);
});
test("a destroyed catalog does not publish a late native discovery result",async()=>{
  let release,changed=0;const initial=catalog(),module=create({snapshot:()=>({catalog:initial}),discover:()=>new Promise(resolve=>release=resolve)});module.subscribe(()=>changed++);const pending=module.refresh();await Promise.resolve();module.destroy();release({runtimes:[]});await pending;assert.equal(changed,1);assert.equal(module.list(),initial);
});
module.exports={catalog};
