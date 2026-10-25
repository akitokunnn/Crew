"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/settings-ui.js"),"utf8"),sandbox);
const ui=sandbox.window.CrewSettings, plain=value=>JSON.parse(JSON.stringify(value));
const catalog=[{id:"claude",name:"Claude Code",available:true,models:[{id:"native-a",name:"Native A",efforts:["low","high"]}],efforts:["low","high"]},{id:"codex",name:"Codex",available:true,models:[{id:"native-b",efforts:["deep"]}],efforts:["deep"]}];
const config=()=>({default:{runtime:null,model:null,effort:null},leader:null,member:null,digest:null,git:null,operations:null});
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
test("each explicit task names its missing runtime when two installed carriers make automatic selection ambiguous",()=>{
  for(const [purpose,label] of [["digest","频道摘要"],["git","Git 管理员"],["operations","运维 AI"]]){
    const ai=config();ai.default.runtime="codex";ai[purpose]={runtime:null,model:null,effort:null};
    assert.throws(()=>ui.validate(ai,catalog),error=>error.message.includes(label)&&error.message.includes("请选择执行载体"));
    ai[purpose]=null;assert.doesNotThrow(()=>ui.validate(ai,catalog));
  }
});

test("operations can choose its own carrier without requiring a global default",()=>{
  const ai=config();ai.operations={runtime:"codex",model:null,effort:null};
  assert.deepEqual(plain(ui.validate(ai,catalog)),ai);
  assert.equal(ai.default.runtime,null);
  ai.default.model="unassigned-model";
  assert.throws(()=>ui.validate(ai,catalog),/默认大脑.*请选择执行载体/);
});

test("new leaders can choose a brain independently of the default and internal tasks",()=>{
  const ai=config();ai.default.runtime="codex";ai.leader={runtime:"claude",model:"native-a",effort:"high"};
  assert.deepEqual(plain(ui.validate(ai,catalog)),ai);
  const restored=ui.initialAI({ai,runtimes:catalog});assert.deepEqual(plain(restored.leader),ai.leader);assert.equal(restored.digest,null);
});
test("an automatic sole task runtime supplies its actual model list and model-specific effort choices",()=>{
  const sole=[{...catalog[0],available:false},{...catalog[1],models:[...catalog[1].models,{id:"native-c",efforts:["light"]}]}],selection={runtime:null,model:"native-b",effort:"deep"};
  const html=ui.selectionMarkup("digest",selection,sole);assert(html.includes('value="native-c"'));assert(html.includes('>native-b</option>'));assert(html.includes('>deep</option>'));assert(!html.includes('value="native-a"'));assert(!html.includes('value="high"'));
  const ai=config();ai.default.runtime="codex";ai.digest=selection;assert.deepEqual(plain(ui.validate(ai,sole).digest),selection);
});
test("changing a model under automatic sole runtime refreshes effort options and saves the user's explicit compatible choice",async()=>{
  const handlers={},nodes=new Map(),picker={tagName:"SELECT",innerHTML:""},element=()=>({classList:{add(){},toggle(){}},setAttribute(){},focus(){},textContent:"",innerHTML:"",disabled:false});
  const content={...element(),querySelector:()=>picker},root={...element(),contains:()=>true,querySelectorAll:()=>[],querySelector(selector){if(selector===".cs-content")return content;if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);},addEventListener:(type,handler)=>handlers[type]=handler};
  const ai=config();ai.default.runtime="codex";ai.digest={runtime:null,model:"native-b",effort:"deep"};const snapshot={ai,runtimes:[{...catalog[0],available:false},{...catalog[1],models:[...catalog[1].models,{id:"native-c",efforts:["light"]}]}]};let saved;
  const settings=ui.create({host:root,read:async()=>snapshot,save:async body=>{saved=plain(body);return {...snapshot,ai:body.ai,execution:body.execution};}});settings.open();for(let i=0;i<12;i++)await Promise.resolve();
  handlers.change({target:{dataset:{aiKey:"digest",aiField:"model"},value:"native-c"}});assert(picker.innerHTML.includes('value="light"'));assert.equal(settings.getDraft().digest.effort,"deep");assert.equal(saved,undefined);
  handlers.change({target:{dataset:{aiKey:"digest",aiField:"effort"},value:"light"}});await settings.save();assert.equal(saved.ai.digest.runtime,null);assert.equal(saved.ai.digest.model,"native-c");assert.equal(saved.ai.digest.effort,"light");
});
test("model and effort suggestions come from catalogs and custom values are escaped instead of replaced",()=>{
  const selection={runtime:"codex",model:'future<model>"',effort:"future&effort"};const html=ui.selectionMarkup("default",selection,catalog);
  assert(html.includes('value="future&lt;model&gt;&quot;"'));assert(html.includes('value="future&amp;effort"'));assert(html.includes('value="native-b"'));
  assert(!html.includes('value="native-a"'));assert(!html.includes("haiku"));assert(html.includes('>原生默认</option>'));
  assert(!html.includes('<datalist'));assert.equal((html.match(/<select /g)||[]).length,3);assert(html.includes('自定义输入'));
  const custom=ui.selectionMarkup("default",selection,catalog,new Set(["default:model"]));
  assert(custom.includes('<input data-ai-key="default" data-ai-field="model"'));assert(custom.includes('返回列表'));
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

function panelHarness(){
  const nodes=new Map(),element=()=>({classList:{add(){},toggle(){}},setAttribute(){},focus(){},textContent:"",innerHTML:"",disabled:false});
  const content={...element(),replaceChildren(){this.innerHTML="";},secretDraft:""};
  const root={...element(),hidden:false,contains:()=>true,querySelectorAll:()=>[],querySelector(selector){if(selector===".cs-content")return content;if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);},addEventListener(){}};
  return{root,content};
}

test("opening message channels does not wait for or start AI model discovery",()=>{
  const {root,content}=panelHarness();let reads=0,mounts=0,cleanups=0;
  const settings=ui.create({host:root,read:()=>{reads++;return new Promise(()=>{});},save:()=>{throw Error("not saving AI settings");},panels:{connections(host){mounts++;host.secretDraft="new-user-secret";return()=>{cleanups++;};}}});
  settings.open("connections");
  assert.equal(reads,0);assert.equal(mounts,1);assert.equal(cleanups,0);assert.equal(content.secretDraft,"new-user-secret");
  settings.hide();assert.equal(cleanups,1);
});

test("late AI discovery cannot remount active message channels or discard their credential draft",async()=>{
  const {root,content}=panelHarness();let resolve,reads=0,mounts=0,cleanups=0;
  const settings=ui.create({host:root,read:()=>{reads++;return new Promise(done=>{resolve=done;});},save:()=>{throw Error("not saving AI settings");},panels:{connections(host){mounts++;host.secretDraft="";return()=>{cleanups++;host.secretDraft="";};}}});
  settings.open("ai");assert.equal(reads,1);settings.open("connections");content.secretDraft="typed-while-discovery-pending";
  resolve({ai:config(),runtimes:catalog});for(let i=0;i<12;i++)await Promise.resolve();
  assert.equal(mounts,1);assert.equal(cleanups,0);assert.equal(content.secretDraft,"typed-while-discovery-pending");
  settings.hide();assert.equal(cleanups,1);
});
