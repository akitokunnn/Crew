"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create,createDraft}=require("../crew/web/leader-create.js"),{create:createCatalog}=require("../crew/web/runtime-catalog.js");
const rows=()=>[{id:"claude",name:"Claude Code",available:true,models:[{id:"opus",name:"Opus"}],efforts:["high"]},{id:"codex",name:"Codex",available:true,models:[{id:"gpt",name:"GPT",efforts:["medium"]}],efforts:["medium"]}];
function data(){const value={catalog:rows(),choices:{}},catalog=createCatalog({snapshot:()=>value,discover:async()=>({runtimes:value.catalog})});return{value,catalog};}
test("leader draft chooses a sole carrier, requires a choice for two, and never follows installation order",()=>{
  for(const installed of [["claude"],["codex"],["claude","codex"],[]]){
    const {value,catalog}=data();for(const row of value.catalog)row.available=installed.includes(row.id);const draft=createDraft({catalog,selection:()=>null});
    assert.equal(draft.snapshot().runtime,installed.length===1?installed[0]:"");
    if(installed.length===1)assert.equal(draft.payload().runtime,installed[0]);else assert.throws(()=>draft.payload(),installed.length?/选择默认载体/:/没有可用的执行工具/);
  }
});
test("catalog refresh reconciles only automatic choices and preserves both text fields and explicit custom native choices",()=>{
  const {value,catalog}=data(),draft=createDraft({catalog,selection:()=>value.choices.default});draft.input("name","  组长  ");draft.input("task","  跑通录音验证  ");
  value.catalog[0].available=false;draft.reconcile();assert.equal(draft.snapshot().runtime,"codex");assert.equal(draft.payload().name,"组长");assert.equal(draft.snapshot().task,"  跑通录音验证  ");
  draft.select("model","future-native");draft.select("effort","deep");catalog.replace(rows());draft.reconcile();assert.deepEqual(draft.payload(),{name:"组长",task:"跑通录音验证",runtime:"codex",model:"future-native",effort:"deep"});
  value.choices.default={runtime:"claude"};draft.reconcile();assert.equal(draft.snapshot().runtime,"codex");
});
test("configured custom choices survive refresh and known native capability failures remain explicit",()=>{
  const {value,catalog}=data();value.choices.leader={runtime:"codex",model:"future-native",effort:"deep"};const draft=createDraft({catalog,selection:()=>value.choices.leader});
  assert.equal(draft.payload().model,"future-native");draft.select("model","gpt");draft.select("effort","high");assert.throws(()=>draft.payload(),/不支持/);
  const next=rows();next[1].available=false;next[1].error="Codex sign-in required";catalog.replace(next);assert.throws(()=>draft.payload(),/Codex sign-in required/);
});
function fixture(overrides={}){
  const {value,catalog}=data(),events=new Map(),keys=new Map(),classes=new Set(),nodes=new Map();let restores=0,refreshes=0;const calls=[],navigation=[];
  const node=()=>({hidden:false,textContent:"",innerHTML:"",value:"",disabled:false,setAttribute(){},focus(){}});
  for(const id of ["leader-runtime-options","leader-runtime-status","leader-model-options","leader-effort-note","leader-effort-options","leader-error","leader-name"])nodes.set("#"+id,node());
  const submit=node(),form={elements:{runtime:node(),model:node(),effort:node()},querySelector:()=>submit};nodes.set("#leader-form",form);nodes.set('[data-act="leader-refresh"]',node());
  const portal={dataset:{},innerHTML:"",classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)},querySelector:selector=>nodes.get(selector),querySelectorAll:()=>[],addEventListener:(type,fn)=>events.set(type,fn),removeEventListener:type=>events.delete(type)};
  const doc={addEventListener:(type,fn)=>keys.set(type,fn),removeEventListener:type=>keys.delete(type)};
  const ports={portal,document:doc,catalog,selection:()=>value.choices.leader??value.choices.default,focus:{capture:()=>"opener",restore:()=>restores++},
    createLeader:async payload=>{calls.push(payload);return{session_id:"created"};},refresh:async()=>refreshes++,navigate:id=>navigation.push(id),...overrides};
  const module=create(ports);
  return{module,catalog,value,calls,navigation,events,keys,classes,ports,restores:()=>restores,refreshes:()=>refreshes,
    fill(){events.get("input")({target:{name:"name",value:"  组长  "}});events.get("input")({target:{name:"task",value:"  联调任务  "}});module.select("runtime","codex");module.select("model","gpt");module.select("effort","medium");}};
}
test("creation owns the form request, suppresses duplicate submits and navigates only after its exact receipt",async()=>{
  let release;const f=fixture();f.ports.createLeader=async payload=>{f.calls.push(payload);return new Promise(resolve=>release=resolve);};f.module.open();f.fill();const first=f.module.submit();await f.module.submit();assert.equal(f.calls.length,1);assert.equal(f.module.snapshot().busy,true);
  release({session_id:"new-leader"});await first;assert.deepEqual(f.calls[0],{name:"组长",task:"联调任务",runtime:"codex",model:"gpt",effort:"medium"});assert.deepEqual(f.navigation,["new-leader"]);assert.equal(f.module.isOpen(),false);
});
test("closing and opening a new draft while an old creation is accepted cannot close or navigate the newer view",async()=>{
  let release;const f=fixture({createLeader:()=>new Promise(resolve=>release=resolve)});f.module.open();f.fill();const pending=f.module.submit();f.module.close();f.module.open();f.fill();release({session_id:"old-created"});await pending;
  assert.equal(f.module.isOpen(),true);assert.equal(f.module.snapshot().draft.name,"  组长  ");assert.deepEqual(f.navigation,[]);assert.equal(f.restores(),1);
});
test("teardown detaches listeners and an accepted late request cannot redraw, notify or navigate",async()=>{
  let release;const f=fixture({createLeader:()=>new Promise(resolve=>release=resolve)});f.module.open();f.fill();const pending=f.module.submit();f.module.destroy();release({session_id:"late"});await pending;
  assert.equal(f.events.size,0);assert.equal(f.keys.size,0);assert.equal(f.classes.has("show"),false);assert.equal(f.refreshes(),0);assert.deepEqual(f.navigation,[]);
});
