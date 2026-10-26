"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
// The reader keeps its header, status and report frame in separate parts; only the header follows later goal state.
const readerText=reader=>[".goal-reader-head",".goal-reader-body",".goal-reader-frame"].map(part=>reader.querySelector(part).innerHTML).join("");
const frameOf=reader=>reader.querySelector(".goal-reader-frame").querySelector("iframe");
function node(){const children=new Map();return {innerHTML:"",hidden:true,dataset:{},scrollTop:0,children:[],get childNodes(){return this.children;},setAttribute(){},focus(){},append(value){value.parent=this;this.children.push(value);this.appended=value;(this.appends??=[]).push(value);},remove(){this.removed=true;if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;},insertAdjacentHTML(position,html){assert.equal(position,"beforebegin");const item=node();item.innerHTML=html;item.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this),0,item);},querySelectorAll(){return [];},querySelector(selector){if(selector==="iframe"&&!this.innerHTML.includes("<iframe"))return null;if(!children.has(selector))children.set(selector,node());return children.get(selector);}};}
function harness(api,{fetch:fetcher}={}){
  const handlers={},drawer=node(),main=node(),calls=[],network=[],navigations=[];
  const state={leaders:[{session_id:"leader",name:"验证组"}],goals:[
    {id:"a",leader_session_id:"leader",title:"短句验证",request:"保留原始要求",status:"running",current_round:1,requirement_version:1,version:1},
    {id:"b",leader_session_id:"leader",title:"性能目标",request:"另一份要求",status:"queued",current_round:1,version:1}]};
  const sandbox={window:{},URLSearchParams,document:{activeElement:null,addEventListener(type,handler){(handlers[type]??=[]).push(handler);},getElementById:id=>id==="goal-drawer"?drawer:id==="main"?main:null,createElement:node,body:{append(){}}},
    fetch:async url=>{network.push(url);return fetcher?fetcher(url):{ok:true,text:async()=>"<h1>真实报告</h1>"};}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),sandbox);
  const client=sandbox.window.CrewGoals.create({project:"fixture",state:()=>state,api:async(url,data)=>{calls.push({url,data});const result=api?await api(url,data):{goal:state.goals.find(item=>url.includes(`id=${item.id}`)),context:{items:[],manifest:{}},reports:[]};return url.startsWith("/api/documents?")?{documents:result.documents||[]}:result;},renderDocuments:items=>items.map(item=>`<span>${item.title}</span>`).join(""),toast(){},onChange(){},onNavigate(id){navigations.push(id);client.sync("leader",id);},onGeneral(){},refreshState:async()=>{}});
  async function click(act,extra={}){const button={disabled:false,dataset:{goalAct:act,...extra}};for(const handler of handlers.click||[])await handler({target:{closest:()=>button}});await tick();}
  async function submit(form){for(const handler of handlers.submit||[])await handler({target:form,preventDefault(){}});await tick();}
  return {module:sandbox.window.CrewGoals,client,drawer,main,state,calls,network,navigations,click,submit};
}

const surfaces=h=>h.client.groupMarkup("leader")+h.client.sideMarkup("leader");

test("ordinary conversation performs no goal reads and cards preserve status, group and escaped requirements",async()=>{
  const h=harness();h.client.sync("leader",null);await tick();assert.equal(h.calls.length,0);assert.equal(h.drawer.hidden,true);
  const card=h.module.cards([{...h.state.goals[0],title:"<script>alert(1)</script>",report_available:false}],h.state.leaders);
  assert(card.includes("&lt;script&gt;"));assert(!card.includes("<script>"));assert(card.includes("验证组"));assert(card.includes("推进中"));assert(!card.includes("完成验收"));
  assert.equal(h.module.status("queued"),"待规划");assert.equal(h.module.status("pausing"),"暂停中");
});

test("opening a goal keeps materials collapsed and same-goal person changes retain the drawer",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();assert.equal(h.calls.length,1);assert.equal(h.drawer.hidden,true);
  const main=surfaces(h);assert(main.includes("需求与验收"));assert(!main.includes("原始需求与验收标准"));assert(main.includes("暂停目标"));assert(!main.includes("goal-context-item"));
  await h.click("drawer",{kind:"requirements"});assert(h.drawer.innerHTML.includes("原始需求与验收标准"));assert(!surfaces(h).includes("原始需求与验收标准"));await h.click("drawer",{kind:"requirements"});
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
  const h=harness(url=>Promise.resolve({goal:{id:"a",title:"短句验证",version:1,current_round:1,requirement_version:1},context:{items:[],manifest:{}},reports:[{id:"r1",title:"第一轮交付",round:1,requirement_version:1,version:1,current:true,status:"completed",summary:"第一轮结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[],url:"https://untrusted.invalid/report"}]}));
  h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"reports"});assert.equal(h.network.length,0);
  await h.click("report",{reportId:"r1"});assert.equal(h.network[0],"/api/goals/report?project=fixture&goal_id=a&report_id=r1");
  assert(h.drawer.innerHTML.includes('sandbox="allow-scripts"'));assert(h.drawer.querySelector("iframe").srcdoc.includes("default-src 'none'"));
  assert(h.drawer.querySelector("iframe").srcdoc.includes("真实报告"));assert(h.calls.every(call=>call.url.startsWith("/api/goal?")||call.url.startsWith("/api/documents?goal_id=")));
  await h.click("expand-report");assert.equal(h.drawer.hidden,true);assert.equal(h.main.appended.className,"goal-report-reader");assert(readerText(h.main.appended).includes("返回交付资料"));assert(readerText(h.main.appended).includes("本轮报告"));assert(readerText(h.main.appended).includes("第 1 轮 · 需求 v1"));assert(h.client.escape());assert.equal(h.main.appended.removed,true);assert.equal(h.drawer.hidden,false);assert(h.drawer.querySelector("iframe").srcdoc.includes("真实报告"));
});

test("cancel needs explicit confirmation and reject requires a preserved human note",async()=>{
  const h=harness(async(url,data)=>data?{goal:{...h.state.goals[0],status:data.action==="reject"?"failed":"cancelled"}}:{goal:{...h.state.goals[0]},context:{items:[],manifest:{}},reports:[]});h.client.sync("leader","a");await tick();
  await h.click("action",{action:"cancel"});assert(!h.calls.some(call=>call.data));await h.click("close-create");assert(!h.calls.some(call=>call.data));
  await h.click("action",{action:"reject"});const error={textContent:""},button={disabled:false};const form={dataset:{goalConfirm:"reject",goalId:"a",version:"1"},elements:{note:{value:""}},hasAttribute:name=>name==="data-goal-confirm",querySelector:selector=>selector===".error-line"?error:button};
  await h.submit(form);assert(!h.calls.some(call=>call.data));assert(error.textContent.includes("修改"));form.elements.note.value="缺少录音对照证据";await h.submit(form);assert.deepEqual(JSON.parse(JSON.stringify(h.calls.find(call=>call.data).data)),{goal_id:"a",action:"reject",version:1,note:"缺少录音对照证据"});
});

test("delivered tasks invite direct follow-up and a new round keeps the earlier human review visible",async()=>{
  const h=harness(async()=>({goal:{...h.state.goals[0],status:"running",current_round:2},
    context:{items:[],manifest:{}},reports:[],acceptance_review:null,
    previous_acceptance_review:{round:1,accepted:false,note:"报告结尾缺少复测证据"}}));
  h.client.sync("leader","a");await tick();
  assert(surfaces(h).includes("第 1 轮 · 你退回修改"));
  assert(surfaces(h).includes("报告结尾缺少复测证据"));
  for(const status of ["completed","failed","awaiting_acceptance"]){
    h.state.goals[0].status=status;h.state.goals[0].version++;
    h.client.invalidate();const markup=surfaces(h);
    assert(markup.includes("直接在下方继续聊"),status);
    assert(markup.includes("已有报告与验收记录保留"));
  }
  assert(h.calls.every(call=>call.data===undefined));
});

test("human acceptance is displayed separately from the unchanged AI report status",async()=>{
  const h=harness(async()=>({goal:{...h.state.goals[0],status:"failed"},acceptance_review:{accepted:false,note:"缺少同一录音的对照",actor:"human"},reports:[{id:"r",title:"原始报告",round:1,requirement_version:1,version:1,current:true,status:"awaiting_acceptance",summary:"AI 原始结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]}],context:{items:[],manifest:{}}}));h.client.sync("leader","a");await tick();const markup=surfaces(h);assert(markup.includes("你退回修改"));assert(markup.includes("缺少同一录音的对照"));assert(markup.includes('data-action="reopen"'));await h.click("drawer",{kind:"reports"});assert(h.drawer.innerHTML.includes("原始报告保持生成时的内容和状态"));assert(h.drawer.innerHTML.includes("AI 报告结果：等待用户验收"));
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
  const h=harness();h.client.sync("leader","a");await tick();await h.click("list");assert(surfaces(h).includes('class="group-goal-list"'));
  assert.equal(h.client.showingList(),true);assert(!surfaces(h).includes('data-goal-detail="a"'));
  assert(surfaces(h).includes('aria-selected="true" tabindex="0" data-goal-act="list"'));
  await h.click("open",{goalId:"a"});const markup=surfaces(h);assert(!markup.includes('class="group-goal-list"'));assert(markup.includes('data-goal-detail="a"'));assert(markup.includes("需求与验收"));
  await h.click("list");assert(surfaces(h).includes('class="group-goal-list"'));h.client.sync("leader","b");await tick();assert(!surfaces(h).includes('class="group-goal-list"'));
});

test("goal details show structured acceptance titles, newest report and explicit reopen or resume actions",async()=>{
  for(const status of ["completed","awaiting_acceptance","waiting_user"]){
    const h=harness(async()=>({goal:{...h.state.goals[0],status,current_round:2,acceptance:[{id:"a1",title:"真实输入例子达标"}]},context:{items:[],manifest:{}},reports:[{id:"new",title:"短句验证",round:2,requirement_version:1,version:1,current:true,status:status==="awaiting_acceptance"?status:"completed",summary:"最新结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]},{id:"old",title:"短句验证",round:1,requirement_version:1,version:1,current:false,status:"failed",summary:"旧轮次结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]}]}));
    h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"requirements"});const markup=surfaces(h);assert(h.drawer.innerHTML.includes("真实输入例子达标"));assert(!h.drawer.innerHTML.includes("[object Object]"));
    assert(markup.includes("最新结论"));assert(!markup.includes("旧轮次结论"),"an older round report is never shown as this round's conclusion");
    if(status==="waiting_user")assert(markup.includes('data-action="resume"'));else if(status==="completed")assert(markup.includes('data-action="reopen"'));else{assert(markup.includes('data-action="accept"'));assert(markup.includes('data-action="reject"'));assert(!markup.includes('data-action="reopen"'));}
    if(status==="awaiting_acceptance")assert(markup.includes("待验收"));
    assert(h.calls.every(call=>call.data===undefined),"opening details does not start or reopen a goal");
  }
});

test("task list selection is explicit and general clears it instead of leaving two selected tabs",async()=>{
  const h=harness();h.client.sync("leader",null);await h.click("list");let markup=surfaces(h);
  assert(h.client.showingList());assert(markup.includes('aria-selected="false" tabindex="-1" data-goal-act="general"'));assert(markup.includes('role="tabpanel"'));
  await h.click("list");assert(h.client.showingList(),"clicking the selected list stays on the list");
  await h.click("general");assert(!h.client.showingList());markup=surfaces(h);assert(markup.includes('aria-selected="true" tabindex="0" data-goal-act="general"'));
});

test("task actions show pending and retained failure feedback and allow a deliberate retry",async()=>{
  let reject;const h=harness(async(url,data)=>data?new Promise((_resolve,fail)=>{reject=fail;}):{goal:{...h.state.goals[0]},executions:[],reports:[],context:{items:[],manifest:{}}});
  h.client.sync("leader","a");await tick();const request=h.click("action",{action:"pause"});await tick();
  assert(surfaces(h).includes("正在处理…"));reject(Error("本轮尚未停止，请稍后重试"));await request;
  const markup=surfaces(h);assert(markup.includes('role="alert"'));assert(markup.includes("本轮尚未停止，请稍后重试"));assert(!markup.includes("正在处理…"));assert(markup.includes("暂停目标"));
});

test("materials show only documents supplied by the selected goal detail and never fetch a global list",async()=>{
  const h=harness(async url=>({goal:{...h.state.goals.find(item=>url.includes(`id=${item.id}`))},reports:[],context:{items:[],manifest:{}},documents:[{id:url.includes("id=a")?1:2,title:url.includes("id=a")?"A任务资料":"B任务资料"}]}));
  h.client.sync("leader","a");await tick();await h.click("drawer",{kind:"reports"});
  assert(h.drawer.innerHTML.includes("相关文档"));assert(h.drawer.innerHTML.includes("A任务资料"));assert(h.calls.every(call=>call.url.startsWith("/api/goal?")||call.url.startsWith("/api/documents?goal_id=")));
  h.client.sync("leader","b");await tick();assert.equal(h.drawer.hidden,true);await h.click("drawer",{kind:"reports"});assert(!h.drawer.innerHTML.includes("A任务资料"));
});

const report=(id,round,extra={})=>({id,title:"短句验证",round,requirement_version:round,version:1,current:false,status:"completed",summary:`第 ${round} 轮结论`,acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[],created_at:"2026-10-25T10:00:00.000Z",...extra});
const deliveryDetail=(h,id,goal,reports)=>({goal:{...h.state.goals.find(item=>item.id===id),...goal},executions:[],context:{items:[],manifest:{}},reports,documents:[]});

test("task cards keep entering a task and reading its delivery as separate sibling buttons",()=>{
  const h=harness(),list=[{...h.state.goals[0],title:"<b>短句</b>",status:"awaiting_acceptance",report_available:true},{...h.state.goals[1],report_available:false},{...h.state.goals[1],id:"c",status:"cancelled",report_available:false}];
  const markup=h.module.cards(list,h.state.leaders,"a");
  let depth=0;for(const [tag] of markup.matchAll(/<\/?button\b/g)){depth+=tag==="<button"?1:-1;assert(depth<=1,"a button never contains another button");}assert.equal(depth,0);
  assert.equal(markup.match(/data-goal-act="open"/g).length,3);
  assert.equal([...markup.matchAll(/data-goal-act="delivery" data-goal-id="([^"]+)"/g)].map(match=>match[1]).join(","),"a","only a goal with a report offers the delivery button");
  assert(markup.includes("&lt;b&gt;短句"));assert(!markup.includes("<b>短句"));assert(markup.includes('aria-pressed="true"'));
  for(const text of ["待验收","待规划","已取消","第 1 轮","验证组","进入任务 →","查看交付报告","交付报告待齐全","保留原始要求"])assert(markup.includes(text),text);
});

test("opening a task shows this round's AI conclusion compactly and reads no report HTML",async()=>{
  const long="第一行结论：短句换人已在同一录音上复测。\n<script>alert(1)</script>"+"补充说明".repeat(40);
  const h=harness(async()=>deliveryDetail(h,"a",{status:"awaiting_acceptance",current_round:2,requirement_version:2},[report("r2",2,{current:true,status:"awaiting_acceptance",summary:long,acceptance_counts:{passed:1,failed:1,unverified:2,simulated:0},unresolved:["混说 <i>仍误判</i>"]}),report("r1",1,{summary:"旧轮次结论"})]));
  h.client.sync("leader","a");await settle();
  assert.equal(h.network.length,0);assert.equal(h.main.appends,undefined);assert.equal(h.drawer.hidden,true);
  let markup=surfaces(h);
  for(const text of ["AI 提交的结论","第 2 轮报告 · 报告结果：等待用户验收","报告验收记录（AI 填写）","共 4 项","通过 1","失败 1","未验证 2","报告补充说明 · 1 条","查看完整报告","展开全文"])assert(markup.includes(text),text);
  assert(!markup.includes("模拟验证"),"a zero count is not listed");assert(markup.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));assert(!markup.includes("<script>"));assert(markup.includes("补充说明".repeat(40)),"the full conclusion stays in place");
  assert(!markup.includes("旧轮次结论"));assert(!markup.includes("混说"),"unresolved details stay folded by default");assert(!markup.includes("你已验收"),"an AI report is not shown as the user's acceptance");
  await h.click("conclusion-toggle",{reportId:"r2"});markup=surfaces(h);
  assert(markup.includes("goal-delivery-summary open"));assert(!markup.includes("混说"));assert(markup.includes("收起"));
  await h.click("report-notes-toggle",{reportId:"r2"});markup=surfaces(h);assert(markup.includes("混说 &lt;i&gt;仍误判&lt;/i&gt;"));
  assert(h.calls.every(call=>call.data===undefined&&call.url.startsWith("/api/goal?")));assert.equal(h.network.length,0);
});

test("five acceptance checks remain five when the report also contains five narrative notes, for either carrier",async()=>{
  for(const runtime of ["claude","codex"]){
    const notes=Array.from({length:5},(_,i)=>`遗留说明 ${i+1} <保留原文>`);
    const h=harness(async()=>deliveryDetail(h,"a",{},[report("counts",1,{current:true,summary:"四项实测通过，一项尚未验证。",acceptance_counts:{passed:4,failed:0,unverified:1,simulated:0},unresolved:notes})]));
    h.state.leaders[0].runtime=runtime;h.client.sync("leader","a");await settle();
    let markup=surfaces(h),meta=markup.match(/<div class="goal-delivery-meta">([\s\S]*?)<\/div>/)[1];
    for(const text of ["共 5 项","通过 4","未验证 1"])assert(meta.includes(text),text);
    assert(!meta.includes("仍未解决"));assert(!meta.includes("补充说明"));assert(markup.includes("报告补充说明 · 5 条"));
    assert(!markup.includes("遗留说明 1"));assert(!markup.includes("仍未解决 5 项"));
    await h.click("report-notes-toggle",{reportId:"counts"});markup=surfaces(h);
    for(let i=1;i<=5;i++)assert(markup.includes(`遗留说明 ${i} &lt;保留原文&gt;`));
    assert(!markup.includes("goal-delivery-summary open"),"notes and conclusion expansion are independent");
    assert(h.calls.every(call=>call.data===undefined),"reading notes never rewrites the stored report");
  }
});

test("all-passed acceptance does not hide report notes or convert them into extra failed checks",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{},[report("passed",1,{current:true,summary:"验收通过。",acceptance_counts:{passed:5,failed:0,unverified:0,simulated:0},unresolved:["已通过场景之外仍有限制"]})]));
  h.client.sync("leader","a");await settle();let markup=surfaces(h);
  assert(markup.includes("共 5 项"));assert(markup.includes("通过 5"));assert(markup.includes("报告补充说明 · 1 条"));assert(!markup.includes('data-kind="failed"'));
  await h.click("report-notes-toggle",{reportId:"passed"});markup=surfaces(h);assert(markup.includes("已通过场景之外仍有限制"));
});

test("failed, unverified and simulated acceptance statuses remain distinct from report notes",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{},[report("mixed",1,{current:true,acceptance_counts:{passed:1,failed:1,unverified:1,simulated:1},unresolved:["附加边界说明"]})]));
  h.client.sync("leader","a");await settle();const markup=surfaces(h);
  for(const text of ["共 4 项","通过 1","失败 1","未验证 1","模拟验证 1","报告补充说明 · 1 条"])assert(markup.includes(text),text);
});

test("a reopened round says it has no new delivery instead of presenting the old report, and goals without reports get no panel",async()=>{
  const h=harness(async url=>url.includes("id=a")?deliveryDetail(h,"a",{status:"running",current_round:2,requirement_version:2},[report("r1",1,{summary:"第一轮结论"})]):deliveryDetail(h,"b",{},[]));
  h.client.sync("leader","a");await settle();let markup=surfaces(h);
  assert(markup.includes("当前第 2 轮暂无新交付"));assert(markup.includes("查看历史报告（第 1 轮）"));assert(!markup.includes("AI 提交的结论"));assert(!markup.includes("第一轮结论"));
  h.client.sync("leader","b");await settle();markup=surfaces(h);assert(!markup.includes("goal-delivery"));assert(!markup.includes("暂无新交付"));assert.equal(h.network.length,0);
});

test("one delivery click from the list opens the selected goal's current report in the isolated reader and returns to the task",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{status:"awaiting_acceptance",current_round:2,requirement_version:2},[report("ra2",2,{current:true,status:"awaiting_acceptance"}),report("ra1",1)]));
  h.client.sync("leader",null);await h.click("list");assert(h.client.showingList());
  await h.click("delivery",{goalId:"a"});await settle();
  assert.deepEqual(h.navigations,["a"]);assert.equal(h.client.showingList(),false);
  assert.equal(h.calls.map(call=>call.url).join(","),"/api/goal?id=a","one detail read for the selected goal");
  assert.equal(h.network.join(","),"/api/goals/report?project=fixture&goal_id=a&report_id=ra2","prefers the current round report");
  const reader=h.main.appended;assert.equal(h.main.appends.length,1);assert.equal(reader.className,"goal-report-reader");
  for(const text of ["返回任务","本轮报告","第 2 轮 · 需求 v2","AI 报告结果：等待用户验收","短句验证 · 交付报告",'sandbox="allow-scripts"'])assert(readerText(reader).includes(text),text);
  assert(!readerText(reader).includes("暂无新交付"));assert(frameOf(reader).srcdoc.includes("default-src 'none'"));assert(frameOf(reader).srcdoc.includes("真实报告"));
  assert(h.calls.every(call=>call.data===undefined),"reading never sends, starts or wakes");assert.equal(h.drawer.hidden,true);
  await h.click("close-report");assert.equal(reader.removed,true);assert.equal(h.drawer.hidden,true);assert.equal(h.client.escape(),false);
  assert(surfaces(h).includes('data-goal-detail="a"'),"returns to the task, not the list");assert.equal(h.network.length,1);assert.equal(h.calls.length,1);
});

test("delivery for a reopened goal opens the newest historical report labelled by round, never as this round's completion",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{status:"queued",current_round:3,requirement_version:3},[report("r2",2,{status:"failed"}),report("r1",1)]));
  await h.click("delivery",{goalId:"a"});await settle();
  const reader=h.main.appended;
  for(const text of ["历史报告","第 2 轮 · 需求 v2","AI 报告结果：未达标","当前第 3 轮暂无新交付"])assert(readerText(reader).includes(text),text);
  assert(!readerText(reader).includes("本轮报告"));assert.equal(h.network.join(","),"/api/goals/report?project=fixture&goal_id=a&report_id=r2");
});

test("failed detail and report reads stay visible with a deliberate retry",async()=>{
  let detailFails=true,htmlFails=true;
  const h=harness(async()=>{if(detailFails){detailFails=false;throw Error("目标详情暂时无法读取");}return deliveryDetail(h,"a",{status:"completed",current_round:1,requirement_version:1},[report("r1",1,{current:true})]);},
    {fetch:async()=>{if(htmlFails){htmlFails=false;return {ok:false,text:async()=>""};}return {ok:true,text:async()=>"<h1>重试后的报告</h1>"};}});
  await h.click("delivery",{goalId:"a"});await settle();
  assert.equal(h.main.appends,undefined);let markup=surfaces(h);
  assert(markup.includes("交付报告未能打开：目标详情暂时无法读取"));assert(markup.includes('role="alert"'));assert(markup.includes('data-goal-act="delivery" data-goal-id="a">重试'));
  await h.click("delivery",{goalId:"a"});await settle();
  const reader=h.main.appended;assert(readerText(reader).includes("报告暂时无法读取，请重试。"));assert(readerText(reader).includes('data-goal-act="retry-report"'));assert.equal(frameOf(reader),null);
  assert(!surfaces(h).includes("交付报告未能打开"));
  await h.click("retry-report");await settle();assert(frameOf(reader).srcdoc.includes("重试后的报告"));assert.equal(h.main.appends.length,1);
  assert.equal(h.network.length,2);assert.deepEqual(h.navigations,["a"]);assert(h.calls.every(call=>call.data===undefined));
});

test("late detail or report HTML never opens on another goal, after closing, or after switching materials",async()=>{
  const ready=(h,id,summary)=>deliveryDetail(h,id,{status:"completed",current_round:1,requirement_version:1},[report("r"+id,1,{current:true,summary})]);
  let release;const late=harness(url=>url.includes("id=a")?new Promise(resolve=>{release=resolve;}):Promise.resolve(ready(late,"b","B 的结论")));
  await late.click("delivery",{goalId:"a"});late.client.sync("leader","b");await settle();
  release(ready(late,"a","A 的结论"));await settle();
  const markup=surfaces(late);assert.equal(late.main.appends,undefined);assert.equal(late.network.length,0);assert(markup.includes("B 的结论"));assert(!markup.includes("A 的结论"));assert(!markup.includes("正在打开交付报告"));

  let html;const switched=harness(url=>Promise.resolve(ready(switched,url.includes("id=a")?"a":"b","结论")),{fetch:()=>new Promise(resolve=>{html=resolve;})});
  await switched.click("delivery",{goalId:"a"});await settle();const reader=switched.main.appended;assert(readerText(reader).includes("正在读取报告"));
  switched.client.sync("leader","b");assert.equal(reader.removed,true);
  html({ok:true,text:async()=>"<h1>A 的报告</h1>"});await settle();assert.equal(switched.main.appends.length,1);assert.equal(frameOf(reader),null);

  let closedHTML;const closed=harness(()=>Promise.resolve(ready(closed,"a","结论")),{fetch:()=>new Promise(resolve=>{closedHTML=resolve;})});
  await closed.click("delivery",{goalId:"a"});await settle();const closedReader=closed.main.appended;await closed.click("close-report");assert.equal(closedReader.removed,true);
  closedHTML({ok:true,text:async()=>"<h1>A 的报告</h1>"});await settle();assert.equal(closed.main.appends.length,1);assert.equal(frameOf(closedReader),null);assert.equal(closed.client.escape(),false);

  let detail;const materials=harness(url=>url.startsWith("/api/goal?")&&!detail?new Promise(resolve=>{detail=resolve;}):Promise.resolve(ready(materials,"a","结论")));
  await materials.click("delivery",{goalId:"a"});await materials.click("drawer",{kind:"reports"});
  detail(ready(materials,"a","结论"));await settle();assert.equal(materials.main.appends,undefined);assert.equal(materials.drawer.hidden,false);assert.equal(materials.network.length,0);
});

// The task area already rendered behind the reader, as app.js scaffolds it.
const taskArea=h=>{const area=node();area.parent=h.main;h.main.children.push(area);return area;};
function evolving(h,first){
  let detail=deliveryDetail(h,"a",{},first);const goal=h.state.goals[0];
  return {read:async()=>detail,async update(change,reports){Object.assign(goal,change,{version:goal.version+1,updated_at:`t${goal.version+1}`});detail=deliveryDetail(h,"a",{},reports);h.client.sync("leader","a");await settle();}};
}

test("an open report follows the latest goal state without rewriting its frame, and the covered task area stays inert across rebuilds",async()=>{
  let source;const h=harness(()=>source.read());Object.assign(h.state.goals[0],{status:"awaiting_acceptance"});
  source=evolving(h,[report("r1",1,{current:true,status:"awaiting_acceptance",summary:"第一稿"})]);const behind=taskArea(h);
  h.client.sync("leader","a");await settle();await h.click("delivery",{goalId:"a"});await settle();
  const reader=h.main.appended,host=reader.querySelector(".goal-reader-frame"),frame=frameOf(reader),srcdoc=frame.srcdoc;
  for(const text of ["本轮报告","第 1 轮 · 需求 v1 · 第 1 稿","返回任务"])assert(readerText(reader).includes(text),text);
  assert.equal(behind.inert,true,"nothing behind the reader can take focus or input");assert.equal(reader.inert,undefined);
  let rewrites=0;const kept=host.innerHTML;Object.defineProperty(host,"innerHTML",{get:()=>kept,set:()=>{rewrites++;}});
  const unchanged=label=>{assert.equal(frameOf(reader),frame,label);assert.equal(frame.srcdoc,srcdoc,label);assert.equal(rewrites,0,label);assert.equal(reader.removed,undefined,label);assert.equal(h.main.appends.length,1,label);assert.equal(h.network.length,1,label);};
  const head=()=>reader.querySelector(".goal-reader-head").innerHTML;

  await source.update({},[report("r1b",1,{version:2,current:true,status:"awaiting_acceptance",summary:"第二稿"}),report("r1",1,{current:true,status:"awaiting_acceptance"})]);
  assert(head().includes("本轮较早报告"));assert(head().includes("已有更新版本（第 2 稿）"));assert(!head().includes("本轮报告"));unchanged("newer draft in the same round");

  await source.update({status:"running",requirement_version:2},[report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  assert(head().includes("历史报告"));assert(head().includes("需求已更新到 v2，本轮暂无新交付"));unchanged("requirement updated in the same round");

  await source.update({status:"queued",current_round:2,requirement_version:3},[report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  // Reopening changes the chat identity, so app.js rebuilds the task area around the open reader.
  h.client.replaceMain('<div id="chat-head"></div><div id="chat-host"></div>');
  const rebuilt=h.main.children[0];assert.equal(behind.removed,true);assert.equal(h.main.children.at(-1),reader);assert.equal(h.main.children.length,2);assert.equal(rebuilt.inert,true,"the rebuilt task area is covered too");
  assert(head().includes("当前第 2 轮暂无新交付"));unchanged("reopened round");

  await source.update({status:"awaiting_acceptance"},[report("r2",2,{requirement_version:3,current:true,status:"awaiting_acceptance"}),report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  assert(head().includes("历史报告"));assert(head().includes("本轮已有新报告"));unchanged("a new round report arrived");
  assert(h.calls.every(call=>call.data===undefined&&call.url==="/api/goal?id=a"),"only read-only detail refreshes");

  await h.click("close-report");assert.equal(reader.removed,true);assert.equal(rebuilt.inert,false,"closing restores the task area");assert(surfaces(h).includes('data-goal-detail="a"'));
});

test("a manually opened earlier draft of this round is labelled as superseded, while viewing delivery reads the newest draft",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{status:"awaiting_acceptance"},[report("r1b",1,{version:2,current:true,status:"awaiting_acceptance",summary:"第二稿结论"}),report("r1",1,{current:true,status:"failed",summary:"第一稿结论"})]));
  h.client.sync("leader","a");await settle();const markup=surfaces(h);assert(markup.includes("第二稿结论"));assert(!markup.includes("第一稿结论"));
  await h.click("drawer",{kind:"reports"});assert(h.drawer.innerHTML.includes("第 1 轮 · 第 2 稿 · 本轮报告"));assert(h.drawer.innerHTML.includes("第 1 轮 · 第 1 稿 · 本轮较早报告"));
  await h.click("report",{reportId:"r1"});await settle();await h.click("expand-report");
  const manual=h.main.appended;for(const text of ["本轮较早报告","已有更新版本（第 2 稿）","第 1 轮 · 需求 v1 · 第 1 稿","AI 报告结果：未达标","返回交付资料"])assert(readerText(manual).includes(text),text);assert(!readerText(manual).includes("本轮报告"));
  assert(h.client.escape());assert.equal(manual.removed,true);assert.equal(h.drawer.hidden,false);
  await h.click("close-drawer");await h.click("delivery",{goalId:"a"});await settle();
  assert.equal(h.network.at(-1),"/api/goals/report?project=fixture&goal_id=a&report_id=r1b");assert(readerText(h.main.appended).includes("本轮报告"));assert(readerText(h.main.appended).includes("第 1 稿")===false);
});

test("direct and manual readers return the same way after a background update, and navigating to another goal still closes them",async()=>{
  for(const mode of ["direct","drawer"]){
    let source;const h=harness(()=>source.read());source=evolving(h,[report("r1",1,{current:true,status:"completed"})]);
    h.client.sync("leader","a");await settle();taskArea(h);
    if(mode==="direct")await h.click("delivery",{goalId:"a"});else{await h.click("drawer",{kind:"reports"});await h.click("report",{reportId:"r1"});await settle();await h.click("expand-report");}
    await settle();const reader=h.main.appended,frame=frameOf(reader);assert(frame.srcdoc.includes("真实报告"),mode);
    await source.update({requirement_version:2},[report("r1",1,{requirement_version:1})]);h.client.replaceMain('<div id="chat-head"></div>');const rebuilt=h.main.children[0];
    assert(readerText(reader).includes("需求已更新到 v2，本轮暂无新交付"),mode);assert.equal(frameOf(reader),frame,mode);assert.equal(rebuilt.inert,true,mode);
    assert(h.client.escape(),mode);assert.equal(reader.removed,true,mode);assert.equal(rebuilt.inert,false,mode);
    if(mode==="direct"){assert.equal(h.drawer.hidden,true);assert(surfaces(h).includes('data-goal-detail="a"'));}
    else{assert.equal(h.drawer.hidden,false);assert(h.drawer.querySelector("iframe").srcdoc.includes("真实报告"),"the materials preview is still there");}
    if(mode==="drawer")await h.click("expand-report");else await h.click("delivery",{goalId:"a"});await settle();
    const again=h.main.appended;assert.notEqual(again,reader);assert.equal(rebuilt.inert,true,mode);
    h.client.sync("leader","b");await settle();assert.equal(again.removed,true,mode+": switching goals closes the reader");assert.equal(rebuilt.inert,false,mode);assert.equal(h.client.escape(),false,mode);
    assert(h.calls.every(call=>call.data===undefined),mode);
  }
});

test("task conversation keeps a single compact heading while controls and delivery remain in the side inspector",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();
  const heading=h.client.groupMarkup("leader"),side=h.client.sideMarkup("leader");
  assert(heading.includes("短句验证"));assert(heading.includes("推进中"));assert(heading.includes('data-goal-act="general"'));assert(heading.includes('data-goal-act="list"'));
  for(const text of ["暂停目标","需求与验收","交付与文档","goal-materials","goal-actions"]){assert(!heading.includes(text),text);assert(side.includes(text),text);}
  h.client.sync("leader",null);const list=h.client.sideMarkup("leader");assert(list.includes('data-goal-id="a"'));assert(list.includes('data-goal-id="b"'));assert(list.includes("查看完整清单"));
});
