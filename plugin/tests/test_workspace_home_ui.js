"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),{window});
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../crew/web/workspace-home.js"),"utf8"), {window});
const ui = window.CrewWorkspaceHome;
const data = patch => ({name:"健身打卡",git_assistant:null,leaders:[],members:[],goals:[],todos:[],decisions:[],...patch});
const render = snapshot => ui.render(snapshot, {settingsIcon:"<svg></svg>",decisionCards:items=>items.map(item=>`decision:${item.id}`).join(";"),taskCards: items => items.map(item=>`task:${item.id}`).join(";"),todoCards:items=>items.map(item=>`todo:${item.id}`).join(";")});

test("overview keeps task states and human TODO separate, without inferring work from processes", () => {
  const input = data({leaders:[{session_id:"L",status:"idle",running:true},{session_id:"discussion",status:"working"}],
    members:[{session_id:"M",status:"working",codex:[{status:"running"},{source:"skill_dispatch",status:"running"}]}],
    goals:[{id:"research",leader_session_id:"L",status:"waiting_user"},{id:"done",leader_session_id:"L",status:"completed",report_available:true}],
    todos:[{id:1,status:"open"},{id:2,status:"done"}],decisions:[{id:1,held:false},{id:2,held:true}]});
  const original=JSON.stringify(input), model=ui.project(input);
  assert.equal(model.activeCount,3);
  assert.equal(model.openGoals[0].id,"research");assert.equal(model.pastGoals[0].id,"done");
  assert.equal(model.discussions.length,1);assert.equal(model.discussions[0].session_id,"discussion");
  assert.equal(model.todos.length,1);assert.equal(model.decisions.length,1);
  assert.equal(model.deliveries.length,1);assert.equal(JSON.stringify(input),original);
});

test("empty workspace invites a discussion, without automatically launching or creating a goal", () => {
  const html=render(data());
  assert.match(html,/开始新对话/);assert.match(html,/data-act="modal-leader"/);
  assert.doesNotMatch(html,/data-goal-act="start"|undefined|NaN/);
});

test("merged decisions retain their type and escape the workspace name and question", () => {
  const html=render(data({name:'<img src=x onerror=alert(1)>',leaders:[{session_id:'leader"',name:"L",status:"idle"}],
    decisions:[{id:1,question:"<script>bad</script>",leader_session_id:'leader"'}]}));
  assert.match(html,/data-kind="decision"/);
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);assert.doesNotMatch(html,/<img|<script>/);
  assert.doesNotMatch(html,/data-act="answer"/);
});

test("awaiting acceptance leads the home with report gist and hands-on steps", () => {
  const steps=[1,2,3,4,5].map(n=>({step:`<b>步骤${n}</b>`,expected:`看到${n}`}));
  const html=render(data({leaders:[{session_id:"L",name:"打卡落地",status:"idle"}],goals:[
    {id:"fit",title:"打卡 App",leader_session_id:"L",status:"awaiting_acceptance",report_available:true,
     acceptance_brief:{report_id:7,summary:"可以打卡了",user_test:steps,supplements_after_report:[]}},
    {id:"old",title:"旧任务",leader_session_id:"L",status:"completed",report_available:true}]}));
  assert.ok(html.indexOf('class="wh-reviews"')<html.indexOf('class="wh-attention"'));assert.ok(html.indexOf('class="wh-reviews"')>html.indexOf('class="wh-primary"'));
  assert.match(html,/待你验收/);assert.match(html,/可以打卡了/);assert.match(html,/打卡落地/);
  assert.match(html,/&lt;b&gt;步骤1&lt;\/b&gt;/);assert.doesNotMatch(html,/<b>步骤/);
  assert.match(html,/预期：看到4/);assert.doesNotMatch(html,/看到5/);assert.match(html,/还有 1 步/);
  assert.match(html,/data-goal-act="delivery" data-goal-id="fit"/);assert.match(html,/data-act="goal-review" data-id="fit"/);
  assert.match(html,/<strong>1<\/strong>待你处理/);
  assert.deepEqual(ui.project(data({goals:[{id:"fit",status:"awaiting_acceptance",report_available:true,acceptance_brief:{summary:"",user_test:[]}}]})).deliveries,[]);
});

test("task delivery stays within its task and completed work is folded", () => {
  const html=render(data({goals:[{id:"fitness",title:"打卡 App",status:"completed",report_available:true,current_round:2}]}));
  assert.match(html,/<details class="wh-history">/);
  assert.match(html,/data-goal-act="delivery" data-goal-id="fitness"/);
  assert.match(html,/第 2 轮/);assert.doesNotMatch(html,/文档中心/);
});

test("home acceptance supplements preserve folding, version order, authors and empty state",()=>{
  const goal={id:"supplement",status:"awaiting_acceptance",acceptance_brief:{summary:"报告摘要",user_test:[],supplements_after_report:[]}},snapshot=data({goals:[goal],members:[{session_id:"m",name:"复核成员"}]});
  assert.doesNotMatch(render(snapshot),/交付后你又补充/);
  goal.acceptance_brief.supplements_after_report=[{version:4,text:"后补 <原文>",actor:"m",at:"2026-11-05T02:00:00Z"},{version:3,text:"先补",actor:"user",at:"2026-11-05T01:00:00Z"}];
  let html=render(snapshot);assert.match(html,/交付后你又补充了 2 条/);assert(!html.includes("后补"));ui.toggleSupplements(goal.id);html=render(snapshot);assert(html.includes("复核成员"));assert(html.includes("后补 &lt;原文&gt;"));assert(html.indexOf("需求 v3")<html.indexOf("需求 v4"));assert(html.includes('datetime="2026-11-05T01:00:00Z"'));ui.toggleSupplements(goal.id);assert(!render(snapshot).includes("后补"));
});
test("home names working sessions and dispatches separately from its task total",()=>{
  const html=render(data({leaders:[{session_id:"L",status:"working",codex:[{source:"skill_dispatch",status:"running"}]}],goals:[{id:"g",leader_session_id:"L",status:"running"}]}));
  assert.match(html,/<strong>1<\/strong>项任务/);assert.match(html,/<strong>2<\/strong>个会话 \/ 派工执行中/);
});
