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
  const handlers={},main=node(),calls=[],network=[],navigations=[];
  const state={git_assistant:null,members:[],leaders:[{session_id:"leader",name:"验证组"}],goals:[
    {id:"a",leader_session_id:"leader",title:"短句验证",request:"保留原始要求",status:"running",current_round:1,requirement_version:1,version:1},
    {id:"b",leader_session_id:"leader",title:"性能目标",request:"另一份要求",status:"queued",current_round:1,version:1}]};
  const sandbox={window:{},URLSearchParams,document:{activeElement:null,addEventListener(type,handler){(handlers[type]??=[]).push(handler);},getElementById:id=>id==="main"?main:null,createElement:node,body:{append(){}}},
    fetch:async url=>{network.push(url);return fetcher?fetcher(url):{ok:true,text:async()=>"<h1>真实报告</h1>"};}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-work-ui.js"),"utf8"),sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),sandbox);
  const client=sandbox.window.CrewGoals.create({project:"fixture",state:()=>state,api:async(url,data)=>{calls.push({url,data});const result=api?await api(url,data):{goal:state.goals.find(item=>url.includes(`id=${item.id}`)),context:{items:[],manifest:{}},reports:[]};if(result.goal)result.goal={supplements_after_report:[],...result.goal};return url.startsWith("/api/documents?")?{documents:result.documents||[]}:result;},renderDocuments:items=>items.map(item=>`<span>${item.title}</span>`).join(""),toast(){},onChange(){},onNavigate(id){navigations.push(id);client.sync("leader",id);},onGeneral(){},refreshState:async()=>{}});
  async function click(act,extra={}){const button={disabled:false,closest:()=>null,dataset:{goalAct:act,...extra}};for(const handler of handlers.click||[])await handler({target:{closest:()=>button}});await tick();}
  async function submit(form){for(const handler of handlers.submit||[])await handler({target:form,preventDefault(){}});await tick();}
  return {module:sandbox.window.CrewGoals,client,main,state,calls,network,navigations,click,submit};
}

const surfaces=h=>h.client.groupMarkup("leader")+h.client.sideMarkup("leader");

test("ordinary conversation performs no goal reads and cards preserve status, group and escaped requirements",async()=>{
  const h=harness();h.client.sync("leader",null);await tick();assert.equal(h.calls.length,0);
  const card=h.module.cards([{...h.state.goals[0],title:"<script>alert(1)</script>",report_available:false}],h.state.leaders);
  assert(card.includes("&lt;script&gt;"));assert(!card.includes("<script>"));assert(card.includes("验证组"));assert(card.includes("推进中"));assert(!card.includes("完成验收"));
  assert.equal(h.module.status("queued"),"待规划");assert.equal(h.module.status("pausing"),"暂停中");
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
  assert(surfaces(h).includes("之前记录 · 你退回修改"));
  assert(surfaces(h).includes("报告结尾缺少复测证据"));
  for(const status of ["completed","failed","awaiting_acceptance"]){
    h.state.goals[0].status=status;h.state.goals[0].version++;
    h.client.invalidate();const markup=surfaces(h);
    assert(markup.includes("点亮任务小框"),status);
    assert(markup.includes("总会话输入框"));
  }
  assert(h.calls.every(call=>call.data===undefined));
});

test("human acceptance is displayed separately from the unchanged AI report status",async()=>{
  const h=harness(async()=>({goal:{...h.state.goals[0],status:"failed"},acceptance_review:{accepted:false,note:"缺少同一录音的对照",actor:"human"},reports:[{id:"r",title:"原始报告",round:1,requirement_version:1,version:1,current:true,status:"awaiting_acceptance",summary:"AI 原始结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]}],context:{items:[],manifest:{}}}));h.client.sync("leader","a");await tick();const markup=surfaces(h);assert(markup.includes("你退回修改"));assert(markup.includes("缺少同一录音的对照"));assert(!markup.includes('data-action="reopen"'));assert(markup.includes("原始交付报告保持生成时的内容和状态"));assert(markup.includes("等待用户验收"));
});


test("execution selection keeps the same round and newest generation across amendments",async()=>{
  const h=harness(async()=>({goal:{...h.state.goals[0],current_round:2,requirement_version:4},context:{items:[],manifest:{}},reports:[],executions:[
    {id:"old-round",session_id:"member",round:1,requirement_version:4,generation:99,runtime:"claude"},
    {id:"old-requirement",session_id:"member",round:2,requirement_version:3,generation:1,runtime:"claude"},
    {id:"previous-generation",session_id:"member",round:2,requirement_version:4,generation:1,runtime:"claude"},
    {id:"selected",session_id:"member",round:2,requirement_version:3,generation:2,runtime:"codex"}]}));
  h.client.sync("leader","a");await tick();assert.equal(h.client.executionFor("member").id,"selected");assert.equal(h.client.executionFor("leader"),null);
  h.client.sync("leader","b");await tick();assert.equal(h.client.executionFor("member"),null);
});


test("selecting a goal collapses its group list while keeping the selected details available",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();await h.click("list");assert(surfaces(h).includes('class="goal-quick-list"'));
  assert.equal(h.client.showingList(),true);assert(!surfaces(h).includes('data-goal-tools="a"'));
  assert(h.client.groupMarkup("leader").includes('aria-expanded="true"'));
  await h.click("open",{goalId:"a"});const markup=surfaces(h);assert(!markup.includes('class="goal-quick-list"'));assert(markup.includes('data-goal-tools="a"'));assert(markup.includes("原始需求与验收标准"));
  await h.click("list");assert(surfaces(h).includes('class="goal-quick-list"'));h.client.sync("leader","b");await tick();assert(!surfaces(h).includes('class="goal-quick-list"'));
});

test("goal details show structured acceptance titles, newest report and explicit reopen or resume actions",async()=>{
  for(const status of ["completed","awaiting_acceptance","waiting_user"]){
    const h=harness(async()=>({goal:{...h.state.goals[0],status,current_round:2,acceptance:[{id:"a1",title:"真实输入例子达标"}]},context:{items:[],manifest:{}},reports:[{id:"new",title:"短句验证",round:2,requirement_version:1,version:1,current:true,status:status==="awaiting_acceptance"?status:"completed",summary:"最新结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]},{id:"old",title:"短句验证",round:1,requirement_version:1,version:1,current:false,status:"failed",summary:"旧轮次结论",acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[]}]}));
    h.client.sync("leader","a");await tick();const markup=surfaces(h);assert(markup.includes("真实输入例子达标"));assert(!markup.includes("[object Object]"));
    assert(markup.includes("最新结论"));assert(!markup.includes("旧轮次结论"),"an older round report is never shown as this round's conclusion");
    if(status==="waiting_user")assert(markup.includes('data-action="resume"'));else if(status==="completed")assert(!markup.includes('data-action="reopen"'));else{assert(markup.includes('data-action="accept"'));assert(markup.includes('data-action="reject"'));assert(!markup.includes('data-action="reopen"'));}
    if(status==="awaiting_acceptance")assert(markup.includes("待验收"));
    assert(h.calls.every(call=>call.data===undefined),"opening details does not start or reopen a goal");
  }
});

test("task list opens in the side panel and the location row has no conversation tabs",async()=>{
  const h=harness();h.client.sync("leader",null);await tick();
  assert(h.client.groupMarkup("leader").includes('data-goal-act="list"'));assert(!h.client.groupMarkup("leader").includes('role="tab"'));
  await h.click("list");assert.equal(h.client.showingList(),true);assert(h.client.sideMarkup("leader").includes('data-goal-id="a"'));
  h.client.sync("leader","a");await tick();assert.equal(h.client.showingList(),false);assert(h.client.sideMarkup("leader").includes('data-goal-tools="a"'));
});
test("task actions show pending and retained failure feedback and allow a deliberate retry",async()=>{
  let reject;const h=harness(async(url,data)=>data?new Promise((_resolve,fail)=>{reject=fail;}):{goal:{...h.state.goals[0]},executions:[],reports:[],context:{items:[],manifest:{}}});
  h.client.sync("leader","a");await tick();const request=h.click("action",{action:"pause"});await tick();
  assert(surfaces(h).includes("正在处理…"));reject(Error("本轮尚未停止，请稍后重试"));await request;
  const markup=surfaces(h);assert(markup.includes('role="alert"'));assert(markup.includes("本轮尚未停止，请稍后重试"));assert(!markup.includes("正在处理…"));assert(markup.includes("暂停目标"));
});

test("delivery lists only documents supplied by the selected goal detail and never fetches a global list",async()=>{
  const h=harness(async url=>({goal:{...h.state.goals.find(item=>url.includes(`id=${item.id}`))},reports:[],context:{items:[],manifest:{}},documents:[{id:url.includes("id=a")?1:2,title:url.includes("id=a")?"A任务资料":"B任务资料"}]}));
  h.client.sync("leader","a");await tick();assert(surfaces(h).includes("A任务资料"));assert(h.calls.every(call=>call.url.startsWith("/api/goal?")||call.url.startsWith("/api/documents?goal_id=")));
  h.client.sync("leader","b");await tick();assert(!surfaces(h).includes("A任务资料"));
});

const report=(id,round,extra={})=>({id,title:"短句验证",round,requirement_version:round,version:1,current:false,status:"completed",summary:`第 ${round} 轮结论`,acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[],created_at:"2026-10-25T10:00:00.000Z",...extra});
const deliveryDetail=(h,id,goal,reports)=>({goal:{...h.state.goals.find(item=>item.id===id),...goal},executions:[],context:{items:[],manifest:{}},reports,documents:[]});

test("compact task rows show state, title, progress and owner and open their task",()=>{
  const h=harness(),markup=h.module.cards([{...h.state.goals[0],title:"<b>短句</b>",work_summary:{total:3,done:1}}],h.state.leaders,"a");
  assert(markup.includes('class="goal-row"'));assert(markup.includes('data-goal-act="open"'));assert(markup.includes('aria-pressed="true"'));assert(markup.includes('max="3" value="1"'));assert(markup.includes("&lt;b&gt;短句"));assert(markup.includes("验证组"));assert(markup.includes("推进中"));assert(!markup.includes('goal-card'));
});
test("opening a task shows this round's AI conclusion compactly and reads no report HTML",async()=>{
  const long="第一行结论：短句换人已在同一录音上复测。\n<script>alert(1)</script>"+"补充说明".repeat(40);
  const h=harness(async()=>deliveryDetail(h,"a",{status:"awaiting_acceptance",current_round:2,requirement_version:2},[report("r2",2,{current:true,status:"awaiting_acceptance",summary:long,acceptance_counts:{passed:1,failed:1,unverified:2,simulated:0},unresolved:["混说 <i>仍误判</i>"]}),report("r1",1,{summary:"旧轮次结论"})]));
  h.client.sync("leader","a");await settle();
  assert.equal(h.network.length,0);assert.equal(h.main.appends,undefined);
  let markup=surfaces(h);
  for(const text of ["本次结果","等待用户验收","AI 验证","共 4 项","通过 1","失败 1","未验证 2","尚存问题 · 1 条","查看完整报告","展开全文"])assert(markup.includes(text),text);
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
    assert(!meta.includes("仍未解决"));assert(!meta.includes("补充说明"));assert(markup.includes("尚存问题 · 5 条"));
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
  assert(markup.includes("共 5 项"));assert(markup.includes("通过 5"));assert(markup.includes("尚存问题 · 1 条"));assert(!markup.includes('data-kind="failed"'));
  await h.click("report-notes-toggle",{reportId:"passed"});markup=surfaces(h);assert(markup.includes("已通过场景之外仍有限制"));
});

test("failed, unverified and simulated acceptance statuses remain distinct from report notes",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{},[report("mixed",1,{current:true,acceptance_counts:{passed:1,failed:1,unverified:1,simulated:1},unresolved:["附加边界说明"]})]));
  h.client.sync("leader","a");await settle();const markup=surfaces(h);
  for(const text of ["共 4 项","通过 1","失败 1","未验证 1","模拟验证 1","尚存问题 · 1 条"])assert(markup.includes(text),text);
});

test("a reopened round says it has no new delivery instead of presenting the old report, and goals without reports get no panel",async()=>{
  const h=harness(async url=>url.includes("id=a")?deliveryDetail(h,"a",{status:"running",current_round:2,requirement_version:2},[report("r1",1,{summary:"第一轮结论"})]):deliveryDetail(h,"b",{},[]));
  h.client.sync("leader","a");await settle();let markup=surfaces(h);
  assert(markup.includes("当前修改尚无新交付"));assert(markup.includes("查看历史版本"));assert(!markup.includes("本次结果"));assert(!markup.includes("第一轮结论"));
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
  for(const text of ["返回任务","当前版本","需求 v2","AI 报告结果：等待用户验收","短句验证 · 交付报告",'sandbox="allow-scripts"'])assert(readerText(reader).includes(text),text);
  assert(!readerText(reader).includes("暂无新交付"));assert(frameOf(reader).srcdoc.includes("default-src 'none'"));assert(frameOf(reader).srcdoc.includes("真实报告"));
  assert(h.calls.every(call=>call.data===undefined),"reading never sends, starts or wakes");
  await h.click("close-report");assert.equal(reader.removed,true);assert.equal(h.client.escape(),false);
  assert(surfaces(h).includes('data-goal-tools="a"'),"returns to the task, not the list");assert.equal(h.network.length,1);assert.equal(h.calls.length,1);
});

test("delivery for a reopened goal opens the newest historical report labelled by round, never as this round's completion",async()=>{
  const h=harness(async()=>deliveryDetail(h,"a",{status:"queued",current_round:3,requirement_version:3},[report("r2",2,{status:"failed"}),report("r1",1)]));
  await h.click("delivery",{goalId:"a"});await settle();
  const reader=h.main.appended;
  for(const text of ["历史报告","需求 v2","AI 报告结果：未达标","当前修改尚无新交付"])assert(readerText(reader).includes(text),text);
  assert(!readerText(reader).includes("当前版本"));assert.equal(h.network.join(","),"/api/goals/report?project=fixture&goal_id=a&report_id=r2");
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

test("late detail or report HTML never opens on another goal or after closing",async()=>{
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
  for(const text of ["当前版本","需求 v1 · 第 1 稿","返回任务"])assert(readerText(reader).includes(text),text);
  assert.equal(behind.inert,true,"nothing behind the reader can take focus or input");assert.equal(reader.inert,undefined);
  let rewrites=0;const kept=host.innerHTML;Object.defineProperty(host,"innerHTML",{get:()=>kept,set:()=>{rewrites++;}});
  const unchanged=label=>{assert.equal(frameOf(reader),frame,label);assert.equal(frame.srcdoc,srcdoc,label);assert.equal(rewrites,0,label);assert.equal(reader.removed,undefined,label);assert.equal(h.main.appends.length,1,label);assert.equal(h.network.length,1,label);};
  const head=()=>reader.querySelector(".goal-reader-head").innerHTML;

  await source.update({},[report("r1b",1,{version:2,current:true,status:"awaiting_acceptance",summary:"第二稿"}),report("r1",1,{current:true,status:"awaiting_acceptance"})]);
  assert(head().includes("较早版本"));assert(head().includes("已有更新版本（第 2 稿）"));assert(!head().includes("当前版本"));unchanged("newer draft in the same round");

  await source.update({status:"running",requirement_version:2},[report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  assert(head().includes("较早版本"));assert(head().includes("已有更新版本（第 2 稿）"));assert(!head().includes("尚无新交付"));unchanged("requirement updated in the same round");

  await source.update({status:"queued",current_round:2,requirement_version:3},[report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  // Reopening changes the chat identity, so app.js rebuilds the task area around the open reader.
  h.client.replaceMain('<div id="chat-head"></div><div id="chat-host"></div>');
  const rebuilt=h.main.children[0];assert.equal(behind.removed,true);assert.equal(h.main.children.at(-1),reader);assert.equal(h.main.children.length,2);assert.equal(rebuilt.inert,true,"the rebuilt task area is covered too");
  assert(head().includes("当前修改尚无新交付"));unchanged("reopened round");

  await source.update({status:"awaiting_acceptance"},[report("r2",2,{requirement_version:3,current:true,status:"awaiting_acceptance"}),report("r1b",1,{version:2,requirement_version:1}),report("r1",1,{requirement_version:1})]);
  assert(head().includes("历史报告"));assert(head().includes("已有更新版本"));unchanged("a new round report arrived");
  assert(h.calls.every(call=>call.data===undefined&&call.url==="/api/goal?id=a"),"only read-only detail refreshes");

  await h.click("close-report");assert.equal(reader.removed,true);assert.equal(rebuilt.inert,false,"closing restores the task area");assert(surfaces(h).includes('data-goal-tools="a"'));
});



test("the location row keeps its task controls and the side panel contains requirements, progress and delivery",async()=>{
  const h=harness();h.client.sync("leader","a");await tick();
  const heading=h.client.groupMarkup("leader"),side=h.client.sideMarkup("leader");
  assert(!heading.includes("短句验证"));assert(!heading.includes('data-goal-act="general"'));assert(heading.includes('data-goal-act="list"'));assert(heading.includes('data-goal-act="mode"'));
  for(const text of ["暂停目标","原始需求与验收标准","goal-actions"]){assert(!heading.includes(text),text);assert(side.includes(text),text);}
  assert(side.indexOf('task-requirements')<side.indexOf('task-progress'));assert(side.indexOf('task-progress')<side.indexOf('task-delivery'));assert(!side.includes('<textarea'));for(const retired of ['data-goal-act="drawer"','data-goal-act="versions"',"goal-materials"])assert(!side.includes(retired),retired);
});
test("acceptance supplements stay folded, expand in version order and preserve authors and original text",async()=>{
  const items=[{version:3,text:"成员补充 <原文>",actor:"member",at:"2026-11-05T02:00:00Z"},{version:2,text:"第一条\n保持换行",actor:"user",at:"2026-11-05T01:00:00Z"}];
  const h=harness(async()=>deliveryDetail(h,"a",{status:"awaiting_acceptance",supplements_after_report:items},[report("r",1,{summary:"已交付摘要"})]));h.state.members=[{session_id:"member",name:"校对成员"}];h.client.sync("leader","a");await settle();
  let html=surfaces(h);assert(html.includes("交付后你又补充了 2 条"));assert(!html.includes("成员补充"));assert(html.indexOf("已交付摘要")<html.indexOf("交付后你又补充"));
  await h.click("supplements-toggle");html=surfaces(h);assert(html.includes("成员补充 &lt;原文&gt;"));assert(html.includes("校对成员"));assert(html.includes("第一条\n保持换行"));assert(html.indexOf("需求 v2")<html.indexOf("需求 v3"));assert(html.includes('datetime="2026-11-05T01:00:00Z"'));
  h.client.sync("leader","a");assert(surfaces(h).includes('aria-expanded="true"'));await h.click("supplements-toggle");assert(!surfaces(h).includes("成员补充"));assert.equal(h.calls.filter(call=>call.data).length,0);
});
test("empty acceptance supplements show no expansion and owners include older requirements only within this round",async()=>{
  const h=harness(async()=>({...deliveryDetail(h,"a",{status:"awaiting_acceptance",requirement_version:5,supplements_after_report:[]},[report("r",1)]),executions:[{session_id:"older",round:1,requirement_version:2,status:"running"},{session_id:"previous",round:0,requirement_version:5,status:"running"}]}));h.state.members=[{session_id:"older",name:"本轮旧需求执行"},{session_id:"previous",name:"上一轮执行"}];h.client.sync("leader","a");await settle();const html=surfaces(h);assert(!html.includes("交付后你又补充"));assert(html.includes("本轮旧需求执行"));assert(!html.includes("上一轮执行"));
});
