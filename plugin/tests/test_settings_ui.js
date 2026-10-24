"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/settings-ui.js"),"utf8"),sandbox);
const ui=sandbox.window.CrewSettings, plain=value=>JSON.parse(JSON.stringify(value));
const catalog=[{id:"claude",name:"Claude Code",available:true,models:[{id:"native-a",name:"Native A",efforts:["low","high"]}],efforts:["low","high"]},{id:"codex",name:"Codex",available:true,models:[{id:"native-b",efforts:["deep"]}],efforts:["deep"]}];
const config=()=>({default:{runtime:null,model:null,effort:null},digest:null,git:null,operations:null});
test("settings never chooses between two installed tools and follows a sole available tool",()=>{
  assert.equal(ui.defaultRuntime(config(),catalog),"");
  assert.equal(ui.defaultRuntime(config(),[catalog[1]]),"codex");
  assert.equal(ui.defaultRuntime(config(),[]),"");
  assert.equal(ui.defaultRuntime({default:{runtime:"claude"}},[{...catalog[0],available:false},catalog[1]]),"claude");
});
test("initial configuration keeps native nulls and independent task overrides without mutating the snapshot",()=>{
  const ai=config();ai.digest={runtime:"claude",model:"future-model",effort:"future-effort"};
  const result=ui.initialAI({ai,runtimes:[catalog[1]]});assert.equal(result.default.runtime,"codex");assert.equal(result.default.model,null);assert.equal(result.default.effort,null);
  assert.deepEqual(plain(result.digest),ai.digest);assert.equal(result.git,null);assert.equal(result.operations,null);assert.equal(ai.default.runtime,null);
});
test("settings validates the actual known model capabilities while retaining future explicit identifiers",()=>{
  const ai=config();assert.throws(()=>ui.validate(ai,catalog),/选择默认载体/);assert.throws(()=>ui.validate(ai,[]),/没有可用/);
  ai.default={runtime:"codex",model:"future-native",effort:"future-deep"};assert.deepEqual(plain(ui.validate(ai,catalog)),ai);
  ai.git={runtime:"claude",model:"native-a",effort:"deep"};assert.throws(()=>ui.validate(ai,catalog),/不支持/);
  ai.git.effort="high";assert.equal(ui.validate(ai,catalog).git.effort,"high");
});
test("model and effort suggestions come from catalogs and custom values are escaped instead of replaced",()=>{
  const selection={runtime:"codex",model:'future<model>"',effort:"future&effort"};const html=ui.selectionMarkup("default",selection,catalog);
  assert(html.includes('value="future&lt;model&gt;&quot;"'));assert(html.includes('value="future&amp;effort"'));assert(html.includes('value="native-b"'));
  assert(!html.includes('value="native-a"'));assert(!html.includes("haiku"));assert(html.includes('placeholder="原生默认"'));
});
test("execution permissions start safely and retain an explicitly saved full-access choice",()=>{
  const safe=ui.initialExecution({});assert.deepEqual(plain(safe),{codex:"workspace-write",claude:"default"});
  const html=ui.executionMarkup(safe);assert(html.includes('value="workspace-write" selected'));assert(html.includes('value="default" selected'));
  assert(!html.includes('value="danger-full-access" selected'));assert(!html.includes('value="bypassPermissions" selected'));
  for(const mode of ["read-only","workspace-write","danger-full-access","default","acceptEdits","bypassPermissions"])assert(html.includes(`value="${mode}"`));
  assert(html.includes("项目外文件和网络"));assert(html.includes("下一次启动载体时生效，不主动重启或唤醒"));
  const explicit={codex:"danger-full-access",claude:"bypassPermissions"};assert.deepEqual(plain(ui.validateExecution(ui.initialExecution({execution:explicit}))),explicit);
  assert.throws(()=>ui.validateExecution({codex:"unknown",claude:"default"}),/有效的执行权限/);
});
