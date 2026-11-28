"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = file => fs.readFileSync(path.join(__dirname,"../..",file),"utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

function element() {
  return {handlers:{},innerHTML:"",hidden:true,dataset:{},children:[],
    addEventListener(type,fn){this.handlers[type]=fn;}, replaceChildren(...children){this.children=children;},
    append(...children){this.children.push(...children);},contains:()=>false,querySelector:()=>null,
    setAttribute(){},focus(){},remove(){},showModal(){this.open=true;},close(){this.open=false;}};
}
const base = {session_id:"a",name:"小叶",seed:"gentle",content:"已有的性格",version:3,percent:80,limit:20000,enabled:true,
  presets:[{key:"gentle",name:"温和耐心",summary:"慢慢说清楚。"}],frozen:false,growing:false};
async function panel(override={},respond) {
  let saved = {...base,...override};
  const calls = [], root = element(), host = element();
  const sandbox = {window:{CrewReportMarkdown:{render:text=>text}},document:{createElement:()=>root},clearTimeout,setTimeout};
  vm.runInNewContext(source("frontend/persona-ui.js"),sandbox);
  const close = sandbox.window.CrewPersona.mount(host,{session_id:"a",request:async (op,body)=>{
    calls.push([op,plain(body)]);
    if (op === "persona.get") return {...saved};
    if (respond) return respond(op,body,saved);
    saved = {...saved,version:saved.version+1};return saved;
  }});
  await settle();
  return {root,calls,close,html:()=>root.innerHTML,
    input(field,value){root.handlers.input({target:{dataset:{field},value}});},
    click:async (ps,data={})=>{
      const button={dataset:{ps,...data},matches:()=>false,querySelector:()=>({textContent:"温和耐心"})};
      await root.handlers.click({target:{closest:()=>button}});
    }};
}

test("replacing an existing start needs confirmation, cancellation writes nothing",async()=>{
  const ui=await panel();
  await ui.click("seed",{key:"gentle"});
  assert.match(ui.html(),/换成「温和耐心」会替换现在的性格，旧内容会留在成长记录里，可以找回。/);
  await ui.click("cancel-seed");
  assert.equal(ui.calls.length,1);
  await ui.click("seed",{key:"gentle"});await ui.click("confirm-seed");
  assert.deepEqual(ui.calls.at(-1),["persona.seed",{session_id:"a",seed:"gentle",expected_version:3}]);
});
test("blank personality accepts a custom start directly and shows the 80 percent warning only at the threshold",async()=>{
  const ui=await panel({content:"",percent:79,seed:null});
  assert.match(ui.html(),/再聊几句就会长出性格，也可以先选个起点。/);
  assert.match(ui.html(),/容量已用 79%（上限 20k，估算）/);assert.match(ui.html(),/data-warning="false"/);
  await ui.click("seed",{key:"custom"});assert.match(ui.html(),/maxlength="1000"/);
  ui.input("seed","请先讲结论");await ui.click("custom-save");
  assert.deepEqual(ui.calls.at(-1),["persona.seed",{session_id:"a",seed:"custom",expected_version:3,seed_text:"请先讲结论"}]);
  assert.match((await panel()).html(),/data-warning="true"[\s\S]*容量已用 80%/);
});
test("a conflict keeps the draft beside the new version and retries only when the user saves",async()=>{
  const ui=await panel({},(op,body,saved)=>{
    assert.equal(op,"persona.save");
    if (body.expected_version===3) {
      saved.version=4;saved.content="刚长出来的说话方式";
      throw Object.assign(Error("版本改变"),{details:{code:"conflict"}});
    }
    return {...saved,version:5,content:body.content};
  });
  await ui.click("edit");ui.input("content","保留 <草稿>");await ui.click("save");
  assert.match(ui.html(),/刚才性格有更新，你的草稿还在，核对后再保存。/);
  assert.match(ui.html(),/刚长出来的说话方式/);assert.match(ui.html(),/保留 &lt;草稿&gt;<\/textarea>/);
  assert.equal(ui.calls.filter(([op])=>op==="persona.save").length,1);
  await ui.click("save");
  assert.deepEqual(ui.calls.at(-1),["persona.save",{session_id:"a",content:"保留 <草稿>",expected_version:4}]);
});
test("history paginates, identifies all authors and restores the requested version",async()=>{
  const rows=["grow","edit","seed","reset","restore"].map((action,i)=>({version:6-i,action,created_at:"2026-11-28T05:00:00Z",summary:"摘要",content:"全文"+i,tokens_estimate:400}));
  const ui=await panel({},(op,body,saved)=>op==="persona.history" ? (body.before===undefined ? {history:rows.slice(0,2),next_before:5} : {history:rows.slice(2),next_before:null}) : saved);
  await ui.click("history");await ui.click("more");
  assert.deepEqual(ui.calls.at(-1),["persona.history",{session_id:"a",limit:10,before:5}]);
  for(const label of ["AI 成长","你改过","选了起点","已重置","恢复","容量 2%"]) assert.ok(ui.html().includes(label));
  await ui.click("version",{version:"4"});assert.match(ui.html(),/全文2/);
  await ui.click("restore",{version:"4"});
  assert.deepEqual(ui.calls.find(([op])=>op==="persona.restore")[1],{session_id:"a",version:4,expected_version:3});
});
test("reset offers both modes for a saved start, and only clear for a blank start",async()=>{
  for (const [button,mode] of [["to-seed","seed"],["clear","clear"]]) {
    const ui=await panel();await ui.click("reset");assert.match(ui.html(),/回到起点/);
    assert.equal(ui.calls.length,1);await ui.click(button);
    assert.deepEqual(ui.calls.at(-1),["persona.reset",{session_id:"a",mode,expected_version:3}]);
  }
  const blank=await panel({seed:"blank"});await blank.click("reset");assert.doesNotMatch(blank.html(),/回到起点/);
});
test("growth state, errors and disabled mode keep their own controls and feedback",async()=>{
  const growing=await panel({growing:true});assert.match(growing.html(),/正在整理最近的对话/);
  for(const action of ["edit","reset"]) assert.match(growing.html(),new RegExp('data-ps="'+action+'" disabled'));
  growing.close();
  for (const [code,message] of [["persona_disabled","打开总开关"],["persona_nothing_new","再聊几句"],["persona_busy","请稍等"],["persona_frozen","取消定型"]]) {
    const ui=await panel({enabled:false,growth_error:"连接超时"},()=>{throw Object.assign(Error("操作失败"),{body:{error_code:code}});});
    assert.match(ui.html(),/上次整理没成功：连接超时/);assert.match(ui.html(),/AI 性格已关闭/);
    assert.doesNotMatch(ui.html(),/data-ps="edit" disabled/);await ui.click("grow");assert.ok(ui.html().includes(message));
  }
});

test("a repeated click starts one growth job and the panel reads its current state",async()=>{
  let finish;
  const ui=await panel({},(_op,_body,saved)=>new Promise(resolve=>{
    finish=()=>{saved.growing=true;resolve({started:true});};
  }));
  const pending=ui.click("grow");await ui.click("grow");finish();await pending;
  assert.deepEqual(ui.calls.map(([op])=>op),["persona.get","persona.grow","persona.get"]);
  assert.match(ui.html(),/正在整理最近的对话/);ui.close();
});

test("the settings directory groups both roles and the switch saves immediately or returns to its saved state",async()=>{
  const root=element(),status=element(),writes=[];
  root.querySelector=()=>status;
  const sandbox={window:{},document:{createElement:()=>root}};
  vm.runInNewContext(source("frontend/persona-ui.js"),sandbox);
  sandbox.window.CrewPersona.mountSettings(element(),{
    request:async(op,body)=>{
      assert.equal(op,"persona.list");assert.deepEqual(plain(body),{});
      return {enabled:true,items:[{project:"甲",subject:"leader",name:"小叶",session_id:"a",frozen:true,percent:80},
        {project:"乙",subject:"workspace_assistant",name:"小林",session_id:"b",growth_error:"超时",percent:0}]};
    },
    save:async body=>{writes.push(plain(body));if(body.persona_enabled)throw Error("写入失败");return body;}
  });
  await settle();assert.match(root.innerHTML,/<h3>甲<\/h3>[\s\S]*小叶[\s\S]*Leader[\s\S]*已定型[\s\S]*容量 80%/);
  assert.match(root.innerHTML,/<h3>乙<\/h3>[\s\S]*小林[\s\S]*工作区助手[\s\S]*成长失败/);
  await root.handlers.change({target:{checked:false}});assert.match(root.innerHTML,/已暂停，内容保留/);
  const input={checked:true};await root.handlers.change({target:input});
  assert.deepEqual(writes,[{persona_enabled:false},{persona_enabled:true}]);
  assert.equal(input.checked,false);assert.equal(status.textContent,"写入失败");
});

function onboarding() {
  const dialog=element(),writes=[],outcomes=[];
  dialog.querySelector=()=>({focus(){},scrollTop:0});
  const sandbox={window:{},document:{body:element(),createElement:()=>dialog}};
  for(const file of ["frontend/workspace-start.js","desktop/src/home-onboarding.js"]) vm.runInNewContext(source(file),sandbox);
  const snapshot={ai:{default:{runtime:"codex"}}};
  sandbox.window.CrewOnboarding.open({bridge:{
    onTheme:()=>()=>{},theme:async()=>({mode:"system",fontScale:1}),
    runtimeReadiness:async()=>({runtimes:["codex","claude"].map(id=>({id,name:id,installed:true,logged_in:true}))}),
    settingsGet:async()=>snapshot,settingsSave:async body=>{writes.push(plain(body));return {...snapshot,...body};},
    onboardingSave:async outcome=>outcomes.push(outcome)
  }});
  return {writes,outcomes,dialog,change:(obField,value)=>dialog.handlers.change({target:{dataset:{obField},value}}),
    async click(obAct){dialog.handlers.click({target:{closest:()=>({dataset:{obAct}})}});await settle();}};
}
test("onboarding always saves the choice on next, with the engine only when changed",async()=>{
  const ui=onboarding();await ui.click("next");
  assert.match(ui.dialog.innerHTML,/data-ob-field="persona" value="true" checked/);
  await ui.click("next");assert.deepEqual(ui.writes,[{persona_enabled:true}]);assert.match(ui.dialog.innerHTML,/进去后先做什么/);
  const other=onboarding();await other.click("next");other.change("persona","false");other.change("engine","claude");await other.click("next");
  assert.deepEqual(other.writes,[{persona_enabled:false,ai:{default:{runtime:"claude",model:null,effort:null,speed:null}}}]);
});
test("skipping the guide after selecting a different option leaves settings untouched",async()=>{
  const ui=onboarding();await ui.click("next");ui.change("persona","false");await ui.click("skip");
  assert.deepEqual(ui.outcomes,["skipped"]);assert.deepEqual(ui.writes,[]);
});

test("system administrator keeps the personality entry hidden, the paired workspace assistant opens its own",async()=>{
  const tag=source("frontend/assistant.html").match(/<button[^>]+id="assistant-persona"[^>]*>/)[0];
  assert.match(tag,/\bhidden\b/);assert.doesNotMatch(tag,/data-local-control/);
  for(const project of [null,"小组"]) {
    const nodes=new Map(), opened=[];
    const document={documentElement:{dataset:{}},addEventListener(){},createElement:element,createTextNode:()=>element(),
      getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);}};
    const sandbox={document,URLSearchParams,location:{hash:""},setTimeout(){},clearTimeout(){},addEventListener(){},
      matchMedia:()=>({matches:false,addEventListener(){}}),localStorage:{getItem:()=>null},
      CrewBrowser:{pageProject:()=>({mode:project?"workspace":"admin",project}),postOptions:()=>({})},
      CrewPersona:{open:options=>opened.push(options),webRequest:()=>()=>{}},CrewSound:{observe(){}},
      CrewChat:{initFiles(){},mount(){},unmount(){}},CrewAccess:{ready:true,local_controls:false},
      fetch:async url=>({ok:true,json:async()=>url.includes("assistant.state")?{assistant:{session_id:"assistant1"}}:{requests:[]}})};
    sandbox.window=sandbox;vm.runInNewContext(source("frontend/assistant.js"),sandbox);await settle();
    const button=document.getElementById("assistant-persona");assert.equal(button.hidden,project===null);
    if(project){button.onclick();assert.equal(opened[0].session_id,"assistant1");}
  }
});
