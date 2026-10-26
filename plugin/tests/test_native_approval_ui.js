"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const approval=extra=>({id:"approval-a",session_id:"member",goal_id:"goal-a",kind:"command",command:"git add -- src",cwd:"C:/project",reason:"Git index lock needs project metadata access",paths:["C:/project/.git/index.lock"],created_at:"2026-10-25T00:00:00Z",status:"pending",...extra});
function harness(api){const handlers={},calls=[],notices=[];let ui,state={leaders:[{session_id:"leader",name:"负责人"}],members:[{session_id:"member",name:"复核员",leader_session_id:"leader"}],goals:[{id:"goal-a",title:"任务 A"},{id:"goal-b",title:"任务 B"}],native_approvals:[approval()]};
  const sandbox={window:{},document:{addEventListener:(type,fn)=>handlers[type]=fn}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/native-approval-ui.js"),"utf8"),sandbox);
  ui=sandbox.window.CrewNativeApprovals.create({api:async(url,body)=>{calls.push({url,body:JSON.parse(JSON.stringify(body))});return api?api(url,body):{approval:{id:body.id,status:body.allow?"approved":"denied"}};},toast:text=>notices.push(text),onChange(){},refreshState:async()=>{state={...state,native_approvals:[]};ui.sync(state);}});ui.sync(state);
  const click=(action,id="approval-a")=>handlers.click({target:{closest:()=>({dataset:{nativeApprovalAct:action,id},disabled:false})}});
  return{ui,state,calls,notices,click};
}
test("native approvals are scoped, escaped and only display a single-command choice without any automatic request",()=>{
  const h=harness();h.state.native_approvals.push(approval({id:"b",goal_id:"goal-b",session_id:"leader",command:"<script>bad</script>"}));h.ui.sync(h.state);
  assert.equal(h.calls.length,0);assert.equal(h.ui.count(),2);assert.equal(h.ui.countForLeader("leader"),2);assert.equal(h.ui.countForChat("leader","goal-a",true),1);assert.equal(h.ui.countForChat("member","goal-b",false),0);assert.equal(h.ui.countForChat("leader",null,false),1);
  const member=h.ui.chatMarkup("member","goal-a",false);assert(member.includes("复核员 · 任务 A"));assert(!member.includes("任务 B"));
  const all=h.ui.markup();assert(all.includes("&lt;script&gt;"));assert(!all.includes("<script>"));assert(all.includes("仅本次命令离开沙箱"));assert(all.includes("允许这一次"));assert(all.includes("拒绝"));assert(!all.includes("acceptForSession"));
  h.state.native_approvals=[approval({kind:"fileChange",command:null})];h.ui.sync(h.state);const fileChange=h.ui.markup();assert(fileChange.includes("仅允许本次列出的文件改动"));assert(!fileChange.includes("仅本次命令离开沙箱"));
});
test("failed human approval stays expanded and retryable, then successful receipt removes it",async()=>{
  let failed=true;const h=harness(async()=>{if(failed)throw Error("原生请求暂不可写，请重试");return{approval:{status:"approved"}};});h.click("allow");await tick();
  assert.equal(h.ui.count(),1);assert(h.ui.markup().includes("原生请求暂不可写，请重试"));assert(h.ui.markup().includes('role="alert"'));assert(!h.ui.markup().includes(" disabled"));
  failed=false;h.click("allow");await tick();assert.equal(h.ui.count(),0);assert.deepEqual(h.calls[1],{url:"/api/native-approval",body:{id:"approval-a",allow:true}});
});
test("a pending approval cannot be submitted twice and ended native requests disappear without an API call",async()=>{
  let release;const h=harness(()=>new Promise(resolve=>{release=resolve;}));h.click("deny");h.click("allow");assert.equal(h.calls.length,1);assert.equal(h.calls[0].body.allow,false);assert(h.ui.markup().includes("正在提交…"));
  release({approval:{status:"denied"}});await tick();h.click("allow");assert.equal(h.calls.length,1);
  h.ui.sync({...h.state,native_approvals:[]});assert.equal(h.ui.markup(),"");h.click("allow");assert.equal(h.calls.length,1);
});
