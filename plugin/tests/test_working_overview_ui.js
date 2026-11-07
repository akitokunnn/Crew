"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");

function harness(snapshot){
  const main={innerHTML:"",dataset:{}};
  const sandbox={window:{},document:{getElementById:()=>main,addEventListener(){}},location:{search:"?project=fixture"},URLSearchParams,
    CrewChat:{renderInline:text=>String(text).replaceAll("<","&lt;").replaceAll(">","&gt;")},
    CrewContext:{ring:()=>""},CrewActivity:{markup:()=>""}};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),sandbox);sandbox.CrewGoals=sandbox.window.CrewGoals;
  const source=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(source.replace("  setup();\n})();",`replace=(node,html)=>{node.innerHTML=html;};
    window.ui={render(snapshot,tab){state=snapshot;allTab=tab;renderAll();return document.getElementById("main").innerHTML;},
      sidebar(snapshot,member=null){state=snapshot;view="chat";leaderId=snapshot.leaders[0].session_id;readOnlyId=member;return sidebarLeader(snapshot.leaders[0],0);},
      node(snapshot){state=snapshot;return leaderNode(snapshot.leaders[0],true);}};
    })();`),sandbox);
  return {render:(tab="tree",next=snapshot)=>sandbox.window.ui.render(next,tab),
    sidebar:(next=snapshot,member=null)=>sandbox.window.ui.sidebar(next,member),node:(next=snapshot)=>sandbox.window.ui.node(next)};
}
const person=(id,extra={})=>({session_id:id,name:id,status:"offline",runtime:"codex",where:"offline",busy:false,
  running:false,active_goal_id:null,context:null,applied:null,model:null,effort:null,codex:[],priority:null,
  task:null,goal:"leader 的任务",pending:0,comments_count:0,...extra});
const state=(leaders,members=[])=>({project:{name:"fixture"},leaders,members,decisions:[],todos:[],goals:[{id:"g1"},{id:"g2"},{id:"g3"}],
  runtimes:[{id:"codex",name:"Codex",models:[],efforts:[]},{id:"claude",name:"Claude Code",models:[],efforts:[]}]});
const count=html=>Number(html.match(/data-tab="working"[^>]*>[\s\S]*?<b>(\d+)<\/b>/)[1]);
const rows=html=>[...html.matchAll(/<(?:button|div) class="li click(?: session-surface)?"[^>]*data-act="([^"]+)"[^>]*data-id="([^"]+)"/g)].map(match=>({action:match[1],id:match[2]}));

test("the screenshot case counts a working leader with offline members and opens the leader's own conversation",()=>{
  const snapshot=state([person("股市掌控者",{status:"working",goal:"只读验证 OpenD 账户与持仓"})],
    [person("独立验收",{leader_session_id:"股市掌控者",group:"股市掌控者"})]);
  const h=harness(snapshot),tree=h.render(),working=h.render("working");
  assert.equal(count(tree),1);assert(tree.includes('class="st working"'));
  assert.equal(count(working),1);assert.deepEqual(rows(working),[{action:"leader",id:"股市掌控者"}]);
  assert(working.includes("只读验证 OpenD 账户与持仓"));assert(working.includes(">leader</span>"));
  assert(!working.includes("undefined"));assert(!working.includes("没有正在干活"));
});

test("both carriers and native locations count each person once even when work belongs to a goal or has subagents",()=>{
  for(const runtime of ["claude","codex"])for(const where of ["bg","vscode"])for(const active_goal_id of [null,"g1"]){
    const item=person("leader",{status:"working",runtime,where,active_goal_id,goal:"目标中的工作",
      codex:[{status:"running",task_name:"子任务一",started_at:"2026-10-26T00:00:00Z"},{status:"running",task_name:"子任务二",started_at:"2026-10-26T00:00:00Z"}]});
    const h=harness(state([item]));assert.equal(count(h.render("working")),1);
    assert.deepEqual(rows(h.render("working")),[{action:"leader",id:"leader"}]);
  }
});

test("the shared total and working list include both roles, retain member ownership, and use each role's actual task field",()=>{
  const snapshot=state([person("leader",{status:"working",goal:"leader <任务>"}),person("idle-leader",{status:"idle",running:true})],
    [person("member",{status:"working",runtime:"claude",leader_session_id:"leader",group:"leader",task:"成员的任务"}),
     person("another",{status:"working",leader_session_id:"leader",group:"leader",task:"另一任务"}),person("offline")]);
  const h=harness(snapshot);assert.equal(count(h.render()),3);
  const html=h.render("working");assert.equal(count(html),3);
  assert.deepEqual(rows(html),[{action:"leader",id:"leader"},{action:"member-chat",id:"member"},{action:"member-chat",id:"another"}]);
  assert(html.includes("leader &lt;任务&gt;"));assert(html.includes("成员的任务"));assert(html.includes("归 leader"));
  assert(!html.includes('data-id="idle-leader"'));assert(!html.includes("undefined"));
});

test("waiting, idle and offline people do not become working just because a native process or a historical goal exists",()=>{
  const snapshot=state([person("idle",{status:"idle",running:true,active_goal_id:"g1"}),person("waiting",{status:"waiting",busy:true})],
    [person("offline",{status:"offline",active_goal_id:"g2"}),person("closed",{status:"closed"})]);
  const html=harness(snapshot).render("working");assert.equal(count(html),0);assert.deepEqual(rows(html),[]);
  assert(html.includes("没有正在干活的会话"));
});

test("state refresh updates both the count and rows without mutating server snapshots",()=>{
  const snapshot=state([person("leader",{status:"working"})],[person("member",{group:"leader",leader_session_id:"leader"})]);
  const original=JSON.stringify(snapshot),h=harness(snapshot);assert.equal(count(h.render("working")),1);
  const next=structuredClone(snapshot);next.leaders[0].status="idle";next.members[0].status="working";
  const html=h.render("working",next);assert.equal(count(html),1);assert.deepEqual(rows(html),[{action:"member-chat",id:"member"}]);
  next.members[0].status="idle";assert.equal(count(h.render("working",next)),0);assert.equal(JSON.stringify(snapshot),original);
});

test("a silent or waiting leader shows its working members in the sidebar and architecture without pretending to work itself",()=>{
  for(const runtime of ["claude","codex"])for(const status of ["idle","waiting","offline"]){
    const snapshot=state([person("leader",{runtime,status,priority:1,pending:1,unread:2}),person("other")],
      [person("worker",{runtime:runtime==="codex"?"claude":"codex",status:"working",leader_session_id:"leader"}),
       person("waiting",{status:"waiting",busy:true,leader_session_id:"leader"}),
       person("someone-else",{status:"working",leader_session_id:"other"})]);
    const original=JSON.stringify(snapshot),h=harness(snapshot),sidebar=h.sidebar(),node=h.node();
    for(const html of [sidebar,node]){
      assert(html.includes('class="group-work-avatar"'));assert(html.includes("1 位组员在干活"));
      assert(!html.includes("本组 2 人在干活"));assert(!html.includes("本人在干活"));
    }
    // The architecture node still counts decisions; the sidebar leader row only marks unread replies with a dot.
    assert(node.includes('class="b-pending"'));assert(!sidebar.includes('class="b-pending"'));assert(sidebar.includes('class="leader-unread-dot"'),"unread replies show as one dot");
    assert(sidebar.includes('aria-current="page"'));assert(sidebar.includes('class="leader-row'));assert(!sidebar.includes('class="b-unread"'));
    const withoutPending=structuredClone(snapshot);withoutPending.leaders[0].pending=0;
    assert(h.node(withoutPending).includes(`class="st ${status}"`));
    assert.equal(JSON.stringify(snapshot),original);
  }
});

test("work halos distinguish self and team work, clear on refresh, and ignore process lifetime, busy waits and past tasks",()=>{
  const snapshot=state([person("leader",{status:"working"})],[person("worker",{status:"working",leader_session_id:"leader"})]);
  const h=harness(snapshot);
  assert(h.sidebar().includes("本组 2 人在干活"));assert(h.node().includes("本组 2 人在干活"));
  const next=structuredClone(snapshot);next.members[0].status="idle";
  assert(h.sidebar(next).includes('class="group-work-avatar"'));assert(h.node(next).includes("本人在干活"));
  next.leaders[0].status="waiting";next.leaders[0].busy=true;next.leaders[0].running=true;next.leaders[0].active_goal_id="g1";
  next.leaders[0].codex=[{status:"running",task_name:"历史条目",started_at:"2026-10-26T00:00:00Z"}];
  for(const html of [h.sidebar(next),h.node(next)])assert(!html.includes('class="group-work-avatar"'));
  assert.equal(count(h.render("working",next)),0);
});

test("an observed skill dispatch keeps the group visible without claiming its idle owner is a working member",()=>{
  for(const runtime of ["claude","codex"]){
    const job={id:"job",source:"skill_dispatch",runtime,status:"running",task_name:"读取验收证据",pid:123,
      started_at:"2026-10-26T00:00:00Z",cwd:"C:/fixture",detail:null};
    const snapshot=state([person("leader",{status:"idle",codex:[job]})]);
    const h=harness(snapshot),node=h.node();
    assert(h.sidebar().includes('class="group-work-avatar"'));assert(node.includes("1 个派工在运行"));
    assert(node.includes('class="st idle"'));assert(node.includes("PID 123"));
    assert(node.includes(runtime==="claude"?"Claude 派活":"Codex 派活"));
    const working=h.render("working");assert.equal(count(working),1);assert(working.includes('data-dispatch-id="job"'));
    assert(working.includes("临时派工 · 由 leader 派出"));assert.deepEqual(rows(working),[{action:"leader",id:"leader"}]);
    const next=structuredClone(snapshot);next.leaders[0].codex[0].status="unknown";
    assert(!h.sidebar(next).includes('class="group-work-avatar"'));assert(!h.node(next).includes("状态未确认"),"historical uncertainty stays in session details, not the live relationship card");
  }
});

test("the sidebar leader row summarises group work, keeps its group during member chats and marks leader autonomy on both carriers",()=>{
  for(const runtime of ["claude","codex"]){
    const job={id:"job",source:"skill_dispatch",runtime,status:"running",task_name:"读取证据",pid:9,started_at:"2026-10-26T00:00:00Z",cwd:"C:/fixture",detail:null};
    const snapshot=state([person("leader",{runtime,status:"working",codex:[job],trust:{enabled:true,goal:"完成登录页改版。数据迁移先问我"}})],
      [person("worker",{status:"working",leader_session_id:"leader"})]);
    snapshot.goals=[{id:"g1",leader_session_id:"leader",status:"running",title:"登录页",pending_decision:null,work_summary:{done:0,total:0}}];
    const h=harness(snapshot),own=h.sidebar();
    assert(own.includes('<i aria-hidden="true"></i>本组 2 人在干活 · 派工 1</span>'));assert(!own.includes('class="st '));
    assert(own.includes('class="trust-mark" data-act="trust-open" data-id="leader" title="自主推进中：完成登录页改版"'));
    assert(own.includes('class="leader-row session-surface active"'));assert(own.includes('aria-current="page"'));assert(own.includes('class="leader-task"'));
    const inMember=h.sidebar(snapshot,"worker");
    assert(inMember.includes('class="leader-row session-surface in-group"'));assert(!inMember.includes('aria-current="page"'));assert(inMember.includes('class="leader-task"'));
    const quiet=structuredClone(snapshot);quiet.leaders[0]={...quiet.leaders[0],status:"waiting",codex:[],trust:{enabled:false,goal:"完成登录页改版"}};quiet.members[0].status="idle";
    const html=h.sidebar(quiet);assert(html.includes('class="st waiting"'));assert(!html.includes("group-work-label"));assert(!html.includes("trust-mark"));
  }
});

