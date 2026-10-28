"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../crew/web/workspace-home.js"),"utf8"), {window});
const ui = window.CrewWorkspaceHome;
const data = patch => ({name:"健身打卡",leaders:[],members:[],goals:[],todos:[],decisions:[],...patch});
const render = snapshot => ui.render(snapshot, {taskCards: items => items.map(item=>`task:${item.id}`).join(";"),todoCards:items=>items.map(item=>`todo:${item.id}`).join(";")});

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

test("decisions lead to their original conversation and untrusted text is escaped", () => {
  const html=render(data({name:'<img src=x onerror=alert(1)>',leaders:[{session_id:'leader"',name:"L",status:"idle"}],
    decisions:[{title:"<script>bad</script>",leader_session_id:'leader"'}]}));
  assert.match(html,/data-act="leader" data-id="leader&quot;"/);
  assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>|<img/);
  assert.doesNotMatch(html,/data-act="answer"/);
});

test("task delivery stays within its task and completed work is folded", () => {
  const html=render(data({goals:[{id:"fitness",title:"打卡 App",status:"completed",report_available:true,current_round:2}]}));
  assert.match(html,/<details class="wh-history">/);
  assert.match(html,/data-goal-act="delivery" data-goal-id="fitness"/);
  assert.match(html,/第 2 轮/);assert.doesNotMatch(html,/文档中心/);
});
