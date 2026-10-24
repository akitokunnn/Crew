"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
function node(){const children=new Map();return {innerHTML:"",hidden:true,dataset:{},scrollTop:0,setAttribute(){},focus(){},remove(){this.removed=true;},querySelectorAll(){return [];},querySelector(selector){if(selector==="iframe"&&!this.innerHTML.includes("<iframe"))return null;if(!children.has(selector))children.set(selector,node());return children.get(selector);}};}
function harness(api){
  const handlers={},drawer=node(),main=node(),calls=[],network=[];
  const state={leaders:[{session_id:"leader",name:"验证组"}],goals:[
    {id:"a",leader_session_id:"leader",title:"短句验证",request:"保留原始要求",status:"running",current_round:1,requirement_version:1,version:1},
    {id:"b",leader_session_id:"leader",title:"性能目标",request:"另一份要求",status:"queued",current_round:1,version:1}]};
  const sandbox={window:{},URLSearchParams,document:{activeElement:null,addEventListener(type,handler){(handlers[type]??=[]).push(handler);},getElementById:id=>id==="goal-drawer"?drawer:id==="main"?main:null,createElement:node,body:{append(){}}},
    fetch:async url=>{network.push(url);return {ok:true,text:async()=>"<h1>真实报告</h1>"};}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),sandbox);
  const client=sandbox.window.CrewGoals.create({project:"fixture",state:()=>state,api:async(url,data)=>{calls.push({url,data});return api?api(url,data):{goal:state.goals.find(item=>url.includes(`id=${item.id}`)),context:{items:[],manifest:{}},reports:[]};},toast(){},onChange(){},onNavigate(){},onGeneral(){},refreshState:async()=>{}});
  async function click(act,extra={}){const button={disabled:false,dataset:{goalAct:act,...extra}};for(const handler of handlers.click||[])await handler({target:{closest:()=>button}});await tick();}
  async function submit(form){for(const handler of handlers.submit||[])await handler({target:form,preventDefault(){}});await tick();}
  return {module:sandbox.window.CrewGoals,client,drawer,main,state,calls,network,click,submit};
}

test("ordinary conversation performs no goal reads and cards preserve status, group and escaped requirements",async()=>{
  const h=harness();h.client.sync("leader",null);await tick();assert.equal(h.calls.length,0);assert.equal(h.drawer.hidden,true);
  const card=h.module.cards([{...h.state.goals[0],title:"<script>alert(1)</script>",report_available:false}],h.state.leaders);
  assert(card.includes("&lt;script&gt;"));assert(!card.includes("<script>"));assert(card.includes("验证组"));assert(card.includes("推进中"));assert(!card.includes("完成验收"));
  assert.equal(h.module.status("queued"),"待规划");assert.equal(h.module.status("pausing"),"暂停中");
});

test("opening a goal keeps materials collapsed and same-goal person changes retain the drawer",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();assert.equal(h.calls.length,1);assert.equal(h.drawer.hidden,true);
  const main=h.client.groupMarkup("leader");assert(main.includes("原始需求与验收标准"));assert(main.includes("暂停目标"));assert(!main.includes("goal-context-item"));
  await h.click("drawer",{kind:"context"});assert.equal(h.drawer.hidden,false);assert(h.drawer.innerHTML.includes("验证组 / 短句验证"));
  h.client.sync("leader","a");assert.equal(h.drawer.hidden,false);assert.equal(h.calls.length,1);
  assert.equal(h.client.escape(),true);assert.equal(h.drawer.hidden,true);assert.equal(h.client.escape(),false);
});

test("a late detail response cannot replace materials from another goal or reopen the old drawer",async()=>{
  let release;
  const h=harness(url=>url.includes("id=a")?new Promise(resolve=>{release=resolve;}):Promise.resolve({goal:{id:"b",title:"性能目标",version:1},context:{items:[{id:2,text:"性能事实",status:"active",applied:false,version:1}],manifest:{}},reports:[]}));
  h.client.sync("leader","a");await h.click("drawer",{kind:"context"});assert.equal(h.drawer.hidden,false);
  h.client.sync("leader","b");assert.equal(h.drawer.hidden,true);await tick();await h.click("drawer",{kind:"context"});
  assert(h.drawer.innerHTML.includes("性能事实"));assert(h.drawer.innerHTML.includes("尚未应用"));
  release({goal:{id:"a",title:"短句验证",version:1},context:{items:[{id:1,text:"旧目标秘密",version:1}],manifest:{}},reports:[]});await tick();
  assert(h.drawer.innerHTML.includes("性能事实"));assert(!h.drawer.innerHTML.includes("旧目标秘密"));assert(!h.drawer.innerHTML.includes("短句验证"));
});

test("report reads use selected goal/report IDs and isolate HTML without invoking a model",async()=>{
  const h=harness(url=>Promise.resolve({goal:{id:"a",title:"短句验证",version:1},context:{items:[],manifest:{}},reports:[{id:"r1",title:"第一轮交付",round:1,status:"completed",url:"https://untrusted.invalid/report"}]}));
  h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"reports"});assert.equal(h.network.length,0);
  await h.click("report",{reportId:"r1"});assert.equal(h.network[0],"/api/goals/report?project=fixture&goal_id=a&report_id=r1");
  assert(h.drawer.innerHTML.includes('sandbox=""'));assert(h.drawer.querySelector("iframe").srcdoc.includes("default-src 'none'"));
  assert(h.drawer.querySelector("iframe").srcdoc.includes("真实报告"));assert(h.calls.every(call=>call.url.startsWith("/api/goal?")));
});

test("context corrections carry the exact item version and keep drafts after a failed write",async()=>{
  const h=harness(async(url,data)=>{if(data)throw Object.assign(Error("stale version"),{status:409});return {goal:{id:"a",title:"短句验证",version:1},context:{items:[{id:"fact",kind:"fact",text:"原始事实",version:7,status:"active",applied:true}],manifest:{}},reports:[]};});
  h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"context"});assert(h.drawer.innerHTML.includes("已应用"));
  await h.click("correct",{itemId:"fact"});assert(h.drawer.innerHTML.includes('data-goal-context-form="fact"'));
  await h.click("expire",{itemId:"fact"});const call=h.calls.find(call=>call.data);assert.deepEqual(JSON.parse(JSON.stringify(call.data)),{goal_id:"a",action:"expire",item_id:"fact",version:7});
  assert(h.drawer.innerHTML.includes('data-goal-context-form="fact"'));
});

test("execution selection never substitutes another goal, requirement version or older generation",async()=>{
  const h=harness(async()=>({goal:{...h.state.goals[0],current_round:2,requirement_version:4},context:{items:[],manifest:{}},reports:[],executions:[
    {id:"old-round",session_id:"member",round:1,requirement_version:4,generation:99,runtime:"claude"},
    {id:"old-requirement",session_id:"member",round:2,requirement_version:3,generation:99,runtime:"claude"},
    {id:"previous-generation",session_id:"member",round:2,requirement_version:4,generation:1,runtime:"claude"},
    {id:"selected",session_id:"member",round:2,requirement_version:4,generation:2,runtime:"codex"}]}));
  h.client.sync("leader","a");await tick();assert.equal(h.client.executionFor("member").id,"selected");assert.equal(h.client.executionFor("leader"),null);
  h.client.sync("leader","b");await tick();assert.equal(h.client.executionFor("member"),null);
});

test("adding a fact uses a typed human source without amending the goal requirements",async()=>{
  const h=harness(async(url,data)=>data?{items:[],manifest:{}}:{goal:{...h.state.goals[0]},context:{items:[],manifest:{}},reports:[]});
  h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"context"});
  await h.submit({hasAttribute:name=>name==="data-goal-context-add",dataset:{},elements:{text:{value:"已验证的观察"}}});
  const call=h.calls.find(call=>call.data);assert.equal(call.url,"/api/goals/context");assert.deepEqual(JSON.parse(JSON.stringify(call.data)),{goal_id:"a",action:"add",kind:"fact",source:{type:"human",id:"a"},text:"已验证的观察"});
});

test("selecting a goal collapses its group list while keeping the selected details available",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();await h.click("list");assert(h.client.groupMarkup("leader").includes('class="group-goal-list"'));
  await h.click("open",{goalId:"a"});const markup=h.client.groupMarkup("leader");assert(!markup.includes('class="group-goal-list"'));assert(markup.includes('data-goal-detail="a"'));assert(markup.includes("原始需求与验收标准"));
  await h.click("list");assert(h.client.groupMarkup("leader").includes('class="group-goal-list"'));h.client.sync("leader","b");await tick();assert(!h.client.groupMarkup("leader").includes('class="group-goal-list"'));
});

test("goal details show structured acceptance titles, newest report and explicit reopen or resume actions",async()=>{
  for(const status of ["completed","awaiting_acceptance","waiting_user"]){
    const h=harness(async()=>({goal:{...h.state.goals[0],status,acceptance:[{id:"a1",title:"真实输入例子达标"}]},context:{items:[],manifest:{}},reports:[{id:"new",title:"最新结论"},{id:"old",title:"旧轮次结论"}]}));
    h.client.sync("leader","a");await tick();const markup=h.client.groupMarkup("leader");assert(markup.includes("真实输入例子达标"));assert(!markup.includes("[object Object]"));
    if(status==="completed"){assert(markup.includes("最新结论"));assert(!markup.includes("旧轮次结论"));}
    if(status==="waiting_user")assert(markup.includes('data-action="resume"'));else assert(markup.includes('data-action="reopen"'));
    if(status==="awaiting_acceptance")assert(markup.includes("待验收"));
    assert(h.calls.every(call=>call.data===undefined),"opening details does not start or reopen a goal");
  }
});
