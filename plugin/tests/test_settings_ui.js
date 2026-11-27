"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const sandbox={window:{},document:{activeElement:null}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../../frontend/settings-ui.js"),"utf8"),sandbox);
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
  for(const [purpose,label] of [["digest","频道摘要"],["git","Git 管理员"],["operations","系统管理员 · 运维"]]){
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
  const handlers={},nodes=new Map(),picker={tagName:"SELECT",innerHTML:""},speedPicker={tagName:"SELECT",innerHTML:""},element=()=>({classList:{add(){},toggle(){}},setAttribute(){},focus(){},textContent:"",innerHTML:"",disabled:false});
  const content={...element(),querySelector:selector=>selector.includes('"speed"')?speedPicker:picker},root={...element(),contains:()=>true,querySelectorAll:()=>[],querySelector(selector){if(selector===".cs-content")return content;if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);},addEventListener:(type,handler)=>handlers[type]=handler};
  const ai=config();ai.default.runtime="codex";ai.digest={runtime:null,model:"native-b",effort:"deep"};const snapshot={ai,runtimes:[{...catalog[0],available:false},{...catalog[1],models:[...catalog[1].models,{id:"native-c",efforts:["light"]}]}]};let saved;
  const settings=ui.create({host:root,read:async()=>snapshot,save:async body=>{saved=plain(body);return {...snapshot,ai:body.ai,execution:body.execution};}});settings.open();for(let i=0;i<12;i++)await Promise.resolve();
  handlers.change({target:{dataset:{aiKey:"digest",aiField:"model"},value:"native-c"}});assert(picker.innerHTML.includes('value="light"'));assert.equal(settings.getDraft().digest.effort,"deep");assert.equal(saved,undefined);
  handlers.change({target:{dataset:{aiKey:"digest",aiField:"effort"},value:"light"}});await settings.save();assert.equal(saved.ai.digest.runtime,null);assert.equal(saved.ai.digest.model,"native-c");assert.equal(saved.ai.digest.effort,"light");
});
test("model and effort suggestions come from catalogs and custom values are escaped instead of replaced",()=>{
  const selection={runtime:"codex",model:'future<model>"',effort:"future&effort"};const html=ui.selectionMarkup("default",selection,catalog);
  assert(html.includes('value="future&lt;model&gt;&quot;"'));assert(html.includes('value="future&amp;effort"'));assert(html.includes('value="native-b"'));
  assert(!html.includes('value="native-a"'));assert(!html.includes("haiku"));assert(html.includes('>原生默认</option>'));
  assert(!html.includes('<datalist'));assert.equal((html.match(/<select /g)||[]).length,4);assert(html.includes('自定义输入'));
  assert(html.includes('data-ai-field="speed"'));assert(html.includes('cs-fields-speed'));
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

test("war room default and advisors share the settings section and save independent choices",async()=>{
  const {root,content}=panelHarness(),handlers={},saved=[];
  root.addEventListener=(type,handler)=>{handlers[type]=handler;};
  const picker={tagName:"SELECT",innerHTML:""};
  content.querySelector=()=>picker;
  const ai={...ui.initialAI({ai:config(),runtimes:catalog}),creative:null,routine:null,difficult:null,
    war_room:{runtime:"claude",model:null,effort:null,speed:null}};
  ai.default.runtime="claude";
  const snapshot={ai,runtimes:catalog};
  const settings=ui.create({host:root,read:async()=>snapshot,save:async body=>{
    saved.push(plain(body.ai));
    return {...snapshot,ai:body.ai,execution:body.execution};
  }});
  settings.open();
  for(let i=0;i<12;i++) await Promise.resolve();
  assert.match(content.innerHTML,/data-ai-task="war_room"[\s\S]*?作战室默认载体[\s\S]*?value="claude" selected/);
  for(const [key,label] of [["creative","创意顾问"],["routine","常规顾问"],["difficult","疑难顾问"]]){
    assert.match(content.innerHTML,new RegExp(`data-ai-task="${key}"[\\s\\S]*?<h3>${label}</h3>`));
    assert.match(content.innerHTML,new RegExp(`data-ai-inherit="${key}" checked>跟随全局默认大脑</label>`));
  }
  assert.equal((content.innerHTML.match(/使用全局默认大脑的载体、模型与思考强度/g) || []).length,3);
  assert.match(content.innerHTML,/data-ai-inherit="assistant" checked>跟随默认<\/label>/);
  const change=target=>handlers.change({target});
  change({dataset:{aiKey:"war_room",aiField:"runtime"},value:"codex"});
  assert.match(content.innerHTML, /data-ai-key="war_room" data-ai-field="model"[\s\S]*?value="native-b"/);
  change({dataset:{aiKey:"war_room",aiField:"model"},value:"native-b"});
  change({dataset:{aiInherit:"creative"},checked:false});
  change({dataset:{aiKey:"creative",aiField:"runtime"},value:"codex"});
  assert.match(content.innerHTML, /data-ai-key="creative" data-ai-field="model"[\s\S]*?value="native-b"/);
  change({dataset:{aiKey:"creative",aiField:"model"},value:"native-b"});
  await settings.save();
  assert.deepEqual(saved[0].creative,{runtime:"codex",model:"native-b",effort:null,speed:null});
  assert.deepEqual(saved[0].war_room,{runtime:"codex",model:"native-b",effort:null,speed:null});
  assert.equal(saved[0].routine,null);
  assert.equal(saved[0].difficult,null);
  change({dataset:{aiInherit:"creative"},checked:true});
  await settings.save();
  assert.equal(saved[1].creative,null);
});

test("opening message channels does not wait for or start AI model discovery",()=>{
  const {root,content}=panelHarness();let reads=0,mounts=0,cleanups=0;
  const settings=ui.create({host:root,read:()=>{reads++;return new Promise(()=>{});},save:()=>{throw Error("not saving AI settings");},panels:{connections(host){mounts++;host.secretDraft="new-user-secret";return()=>{cleanups++;};}}});
  settings.open("connections");
  assert.equal(reads,0);assert.equal(mounts,1);assert.equal(cleanups,0);assert.equal(content.secretDraft,"new-user-secret");
  settings.hide();assert.equal(cleanups,1);
});

test("late AI discovery cannot remount an active self-loading panel or discard its in-progress state",async()=>{
  // Sounds once lost its "saved" confirmation this way when the snapshot finished during a save.
  for(const name of ["connections","sounds","remote"]){
    const {root,content}=panelHarness();let resolve,reads=0,mounts=0,cleanups=0;
    const settings=ui.create({host:root,read:()=>{reads++;return new Promise(done=>{resolve=done;});},save:()=>{throw Error("not saving AI settings");},panels:{[name](host){mounts++;host.secretDraft="";return()=>{cleanups++;host.secretDraft="";};}}});
    settings.open("ai");assert.equal(reads,1);settings.open(name);content.secretDraft="typed-while-discovery-pending";
    resolve({ai:config(),runtimes:catalog});for(let i=0;i<12;i++)await Promise.resolve();
    assert.equal(mounts,1,name);assert.equal(cleanups,0,name);assert.equal(content.secretDraft,"typed-while-discovery-pending",name);
    settings.hide();assert.equal(cleanups,1,name);
  }
});

test("storage opens independently and late model discovery preserves its cleanup preview",async()=>{
  const {root,content}=panelHarness();let resolve,reads=0,mounts=0,cleanups=0;
  const settings=ui.create({host:root,read:()=>{reads++;return new Promise(done=>{resolve=done;});},save:()=>{throw Error("storage does not save AI settings");},panels:{storage(host){mounts++;host.preview=null;return()=>{cleanups++;host.preview=null;};}}});
  settings.open("storage");assert.equal(reads,0);assert.equal(mounts,1);
  settings.open("ai");assert.equal(reads,1);settings.open("storage");
  content.preview={id:"reviewed-cleanup",files:3};const beforeMounts=mounts,beforeCleanups=cleanups;
  resolve({ai:config(),runtimes:catalog});for(let i=0;i<12;i++)await Promise.resolve();
  assert.equal(mounts,beforeMounts);assert.equal(cleanups,beforeCleanups);assert.equal(content.preview.id,"reviewed-cleanup");
  settings.hide();assert.equal(cleanups,beforeCleanups+1);
});
test("Codex speed settings offer inheritance and supported standard and fast speeds, preserving a saved tier",()=>{
  const fast=[catalog[0],{...catalog[1],models:[{id:"native-b",efforts:["deep"],speeds:[{id:"priority",name:"Fast",description:"2x speed"}]},{id:"native-c",efforts:[],speeds:[{id:"priority",name:"Fast"},{id:"ultrafast",name:"Ultrafast"}]}]}];
  const html=ui.selectionMarkup("default",{runtime:"codex",model:"native-c",effort:null,speed:"ultrafast"},fast);
  assert.match(html,/value="ultrafast" selected disabled[^>]*>当前 · 超快<\/option>/);
  assert.match(html,/value="default"[^>]*>标准<\/option>/);
  assert.match(html,/value="priority"[^>]*>快速<\/option>/);
  assert.match(html,/value=""[^>]*>跟随 Codex 设置<\/option>/);
  assert(!ui.selectionMarkup("default",{runtime:"claude",model:null,effort:null,speed:null},fast).includes('data-ai-field="speed"'));
  const unset=ui.selectionMarkup("default",{runtime:"codex",model:null,effort:null,speed:null},fast);assert(unset.includes('value="priority"'));assert(!unset.includes('value="ultrafast"'));
  const selectedModel = ui.selectionMarkup("default",{runtime:"codex",model:"native-c",effort:null,speed:null},fast);
  assert(!selectedModel.includes('value="ultrafast"'));
  const standardOnly = [catalog[0], {...catalog[1], models:[{id:"native-only", efforts:[], speeds:[]}]}];
  assert(!ui.selectionMarkup("default",{runtime:"codex",model:"native-only",effort:null,speed:null},standardOnly).includes('value="priority"'));
  const ai=config();ai.default={runtime:"codex",model:"native-b",effort:null,speed:"ultrafast"};assert.throws(()=>ui.validate(ai,fast),/不支持所选速度/);
  ai.default.speed="priority";assert.equal(ui.validate(ai,fast).default.speed,"priority");
  ai.default={runtime:"claude",model:null,effort:null,speed:"priority"};assert.throws(()=>ui.validate(ai,fast),/只适用于 Codex/);
  assert.equal(ui.validate({...ai, default:{runtime:"codex",model:"native-c",effort:null,speed:"ultrafast"}},fast).default.speed,"ultrafast");
});

test("a LAN host connection labels host settings and offers no LAN access card or local backend wording",()=>{
  const {root,content}=panelHarness();
  ui.create({host:root,remote:true,read:()=>new Promise(()=>{}),save:()=>{},panels:{}}).open("backend");
  assert.match(root.innerHTML,/Crew · 所连主机/);assert.match(root.innerHTML,/所连主机的设置 · 所有工作区共用/);
  assert.doesNotMatch(root.innerHTML,/data-settings-category="remote"/);
  const local=panelHarness();
  ui.create({host:local.root,read:()=>new Promise(()=>{}),save:()=>{},panels:{remote(){}}}).open("remote");
  assert.match(local.root.innerHTML,/Crew · 本机偏好/);assert.match(local.root.innerHTML,/data-settings-category="remote"/);
});

test("the session library is listed only where a page registers its panel",()=>{
  const home=panelHarness();
  ui.create({host:home.root,read:()=>new Promise(()=>{}),save:()=>{},panels:{}}).open("ai");
  assert.doesNotMatch(home.root.innerHTML,/data-settings-category="library"/);
  const workspace=panelHarness();
  ui.create({host:workspace.root,read:()=>new Promise(()=>{}),save:()=>{},panels:{library(){}}}).open("library");
  assert.match(workspace.root.innerHTML,/data-settings-category="library"/);
});
