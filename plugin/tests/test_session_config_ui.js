"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const sandbox = {window:{}}; vm.createContext(sandbox);
for (const file of ["leader-trust-ui.js", "runtime-switch-ui.js"]) vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",file),"utf8"),sandbox);
const trust = sandbox.window.CrewLeaderTrust, switching = sandbox.window.CrewRuntimeSwitch;
const plain = value => JSON.parse(JSON.stringify(value));
const member = extra => ({session_id:"member",runtime:"claude",where:"offline",status:"offline",model:"opus",effort:"max",runtime_switch:{allowed:true},...extra});
const catalog = [
  {id:"claude",name:"Claude Code",available:true,models:[{id:"opus"},{id:"claude-haiku-4-5-20251001"}],efforts:["low","medium","high","max"]},
  {id:"codex",name:"Codex",available:true,models:[{id:"gpt",efforts:["low","medium"]},{id:"image",efforts:[]}],efforts:["low","medium","high"]}
];
const target = extra => ({runtime:"codex",model:null,effort:null,...extra});

test("leader trust requires a specific leader and a goal only when enabled",()=>{
  assert.throws(()=>trust.payload(null,true,"goal"),/leader/);
  assert.throws(()=>trust.payload("leader-a",true,"  "),/目标和授权范围/);
  assert.deepEqual(plain(trust.payload("leader-a",true,"  组A目标  ")),{leader_session_id:"leader-a",enabled:true,goal:"组A目标"});
  assert.deepEqual(plain(trust.payload("leader-b",false,"")),{leader_session_id:"leader-b",enabled:false,goal:""});
});

test("trust display and scope are leader specific and escaped",()=>{
  const a={session_id:'a"',name:"A",trust:{enabled:true,goal:"<script>bad</script>"}},b={session_id:"b",name:"B"};
  assert.equal(trust.trustFor(b).enabled,false);
  assert.match(trust.indicator(a),/data-id="a&quot;"/);
  assert.match(trust.indicator(a),/&lt;script&gt;bad&lt;\/script&gt;/);assert(!trust.indicator(a).includes("<script>"));
  assert.match(trust.indicator(b),/信任 · 关/);
});

test("switching blocks active and external writers before consulting stale allowed state",()=>{
  assert.match(switching.reason(member({where:"vscode"})),/VS Code/);
  assert.match(switching.reason(member({busy:true})),/正在工作/);
  assert.match(switching.reason(member({status:"working"})),/正在工作/);
  assert.equal(switching.reason(member({runtime_switch:{allowed:false,reason:"等待进程排空"}})),"等待进程排空");
  assert.match(switching.reason(member({runtime_switch:undefined})),/暂不支持/);
  assert.equal(switching.reason(member()),"");
});

test("target defaults are explicit nulls and never inherit source model or effort",()=>{
  assert.deepEqual(plain(switching.targetPayload(member(),target(),catalog,null)),{session_id:"member",runtime:"codex",model:null,effort:null});
  assert.throws(()=>switching.targetPayload(member(),target({runtime:"claude"}),catalog,null),/另一个/);
});

test("switch previews reject models and efforts outside the chosen runtime catalog",()=>{
  assert.throws(()=>switching.targetPayload(member(),target({model:"opus"}),catalog,null),/目标模型/);
  assert.throws(()=>switching.targetPayload(member(),target({effort:"high"}),catalog,null),/具体模型/);
  assert.throws(()=>switching.targetPayload(member(),target({model:"gpt",effort:"high"}),catalog,null),/不支持/);
  assert.throws(()=>switching.targetPayload(member(),target({model:"image",effort:"low"}),catalog,null),/不支持/);
  assert.throws(()=>switching.targetPayload(member({runtime:"codex"}),target({runtime:"claude",model:"claude-haiku-4-5-20251001",effort:"low"}),catalog,null),/不支持/);
});

test("the edited handoff is included exactly for a fresh bound preview",()=>{
  const text="原摘要\n人工补充：保留已知限制，不声称已完成。";
  assert.deepEqual(plain(switching.targetPayload(member(),target({model:"gpt",effort:"medium"}),catalog,text)),{session_id:"member",runtime:"codex",model:"gpt",effort:"medium",handoff:text});
});

test("unavailable tools retain their actionable error and cannot prepare",()=>{
  assert.throws(()=>switching.targetPayload(member(),target(),[{...catalog[1],available:false,error:"请先安装 Codex CLI"}],null),/安装 Codex CLI/);
});

test("runtime switch does not preselect an unavailable alternative on a Codex-only installation",()=>{
  const notices=[],overlay={hidden:false,addEventListener(){}},environment={window:{},document:{createElement:()=>overlay,body:{append(){}},addEventListener(){}}};
  vm.createContext(environment);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/runtime-switch-ui.js"),"utf8"),environment);
  const ui=environment.window.CrewRuntimeSwitch.create({getMember:()=>member({runtime:"codex"}),catalog:()=>[{id:"claude",available:false},{id:"codex",available:true}],toast:text=>notices.push(text)});
  ui.open("member");assert.equal(overlay.hidden,true);assert.match(notices[0],/没有可用的其他执行工具/);
});
