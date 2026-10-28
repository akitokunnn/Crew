"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../crew/web/app.js"), "utf8").replace(/\r\n/g, "\n");
const fixture = () => ({models:[{id:"claude-sonnet", name:"Sonnet"}, {id:"claude-haiku-4-5-20251001", name:"Haiku"}],
  efforts:["low", "medium", "high", "max"], runtimes:[
    {id:"claude", name:"Claude Code", available:true, error:null,
      models:[{id:"claude-sonnet", name:"Sonnet"}, {id:"claude-haiku-4-5-20251001", name:"Haiku"}], efforts:["low", "medium", "high", "max"]},
    {id:"codex", name:"Codex", available:true, error:null, models:[
      {id:"gpt-a", name:"GPT A", efforts:["low", "medium"]},
      {id:"gpt-b", name:"GPT B", efforts:["high", "max"], defaultReasoningEffort:"high"},
      {id:"gpt-c", name:"GPT C", efforts:[]},
    ], efforts:["low", "medium", "high", "max"]},
  ]});
const session = extra => ({session_id:"session-codex", runtime:"codex", model:"gpt-a", effort:"medium",
  applied:{model:"gpt-a", effort:"medium"}, where:"bg", running:true, ...extra});
const form = (runtime="codex", model="", effort="") => ({dataset:{runtimeExplicit:"true"},elements:{name:{value:" fixture "}, task:{value:" work "},
  runtime:{value:runtime}, model:{value:model}, effort:{value:effort}},
  submit:{disabled:false}, refresh:{disabled:false, hidden:false},
  querySelector(selector) { return selector === '[type="submit"]' ? this.submit : this.refresh; }});

function harness(state = fixture(), fetcher = () => { throw new Error("Unexpected network request"); }) {
  const nodes = new Map();
  const node = () => ({innerHTML:"", textContent:"", className:"", hidden:false, dataset:{},classList:{contains:()=>false},
    contains:() => false, querySelectorAll:() => [], setAttribute(name, value) { this[name] = value; }});
  for (const id of ["menu", "backdrop", "main", "trust-error", "leader-runtime-options", "leader-runtime-status", "leader-model-options", "leader-effort-note", "leader-effort-options"]) nodes.set(id, node());
  const escape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const sandbox = {window:{}, document:{visibilityState:"visible",hasFocus:()=>true,activeElement:null, getElementById:id => nodes.get(id) || null, addEventListener:() => {}},
    location:{search:"?project=fixture&legacy=1", origin:"http://fixture.local", hash:""}, URLSearchParams, URL, fetch:fetcher, console, setTimeout, clearTimeout,
    CrewChat:{renderInline:escape, renderMarkdown:text => `<p>${escape(text)}</p>`}};
  // Select the fixture transport explicitly for the retained legacy entry.
  const transport = require("../crew/web/crew-client.js").create({environment:{Headers,location:sandbox.location,fetch:fetcher}});
  sandbox.CrewClient = {current:{request:transport.request,fetch:fetcher}};
  vm.createContext(sandbox);
  const contextSource=fs.readFileSync(path.join(__dirname, "../crew/web/chat.js"), "utf8");
  vm.runInContext(contextSource.slice(0, contextSource.indexOf("})();")+5), sandbox);
  sandbox.CrewContext=sandbox.window.CrewContext;
  sandbox.CrewActivity={markup:()=>""};
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web/leader-trust-ui.js"), "utf8"), sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web/attention.js"), "utf8"), sandbox);
  for(const file of ["runtime-catalog.js","runtime-models.js","ui-renderer.js"])vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",file),"utf8"),sandbox);
  sandbox.createComments=require("./comments_fixture.cjs").createComments;
  // Expose lexical UI functions while leaving setup and its state-poll timer off.
  const end = "  setup();\n})();";
  assert(source.endsWith(end + "\n") || source.endsWith(end));
  vm.runInContext(source.replace(end, `  window.ui = {runtimeCatalog:()=>runtimeCatalog,runtimeModels:()=>runtimeModels,chatAccess, goalChatNotes,
    contextRing, contextControls, sidebarLeader, memberNode, workingRow,
    renderTodos, domRenderer:()=>domRenderer, trustReportUrl, readTrustReport, attention:()=>attentionFeature,
    route() {readRoute(); return {view,leaderId,readOnlyId};},
    services(values) {if(values.api)api=values.api;if(values.loadState)loadState=values.loadState;if(values.toast)toast=values.toast;if(values.render)render=values.render;},
    setState(value) {state=value;if(!domRenderer)domRenderer=window.CrewDOMRenderer.create({document});if(!runtimeCatalog)runtimeCatalog=window.CrewRuntimeCatalog.create({snapshot:()=>({catalog:state?.runtimes,legacyModels:state?.models,legacyEfforts:state?.efforts,choices:state?.ai}),discover:()=>api("/api/runtimes")});if(!runtimeModels)runtimeModels=window.CrewRuntimeModels.create({catalog:runtimeCatalog,icon:ic,runtimeBadge,findSession:sessionItem,menu:{show(){},hide(){},anchor:()=>null,isCurrent:()=>false},save(){},refresh(){},notify(){}});if(!commentsFeature)commentsFeature=createComments(()=>({leaders:state?.leaders||[],members:state?.members||[]}),document);if(!attentionFeature)attentionFeature=window.CrewAttention.create({state:()=>state,document,api:(...args)=>api(...args),refresh:()=>loadState(true),notify:(...args)=>toast(...args),changed:()=>{},navigate:()=>{},rich:{inline:CrewChat.renderInline,markdown:CrewChat.renderMarkdown},ui:{icon:ic,avatar:av,priority:priorityBadge,date,ago,empty}});}};\n})();`), sandbox);
  sandbox.window.ui.setState(state);
  const catalog=sandbox.window.ui.runtimeCatalog(),models=sandbox.window.ui.runtimeModels();
  const people=require("./people_fixture.cjs").fixture(()=>({leaders:state.leaders||[],members:state.members||[]}),{modelMenu:models.markup,runtimeId:catalog.id}).module;
  return {ui:{...sandbox.window.ui,...sandbox.window.ui.attention(),replace:sandbox.window.ui.domRenderer().replace,runtimeList:catalog.list,runtimeInfo:catalog.info,defaultRuntime:catalog.defaultRuntime,modelEfforts:catalog.efforts,optionChange:catalog.optionChange,optionsLabel:catalog.optionsLabel,refreshRuntimes:catalog.refresh,modelMenu:models.markup,sessionMenu:people.sessionMenu,editorHandoffReason:people.editorHandoffReason}, nodes, location:sandbox.location};
}
const plain = value => JSON.parse(JSON.stringify(value));

test("collected offline people can start a conversation while known external sessions stay readonly", () => {
  const {ui}=harness();
  for(const role of ["leader","member"]){
    const person={role,web_chat:false,running:false,where:"offline"};
    assert.deepEqual(plain(ui.chatAccess(person,null)),{readonly:false});
    assert.deepEqual(plain(ui.chatAccess({...person,running:true,where:"bg",busy:true},null)),{readonly:false});
    const external=ui.chatAccess({...person,running:true,where:"vscode"},null);assert.equal(external.readonly,true);assert.match(external.readonlyReason,/原工具退出/);
  }
});

test("goal status controls composer access without promising discussion around a manual pause", () => {
  const {ui}=harness(),person={web_chat:false,running:true,where:"vscode"};
  for(const status of ["paused","pausing","cancelled"]){const access=ui.chatAccess(person,{status});assert.equal(access.readonly,true);assert.match(access.readonlyReason,status==="cancelled"?/再开一轮/:/明确恢复/);}
  for(const status of ["queued","running","waiting_user","completed","failed","awaiting_acceptance"])assert.deepEqual(plain(ui.chatAccess(person,{status})),{readonly:false});
});

test("only a queued goal without native execution claims it has not started", () => {
  const {ui}=harness();
  for(const status of ["running","pausing","paused","waiting_user","awaiting_acceptance","completed","failed","cancelled"])assert.deepEqual(plain(ui.goalChatNotes({status},false,true)),{badge:"",hint:""},status);
  const queued=ui.goalChatNotes({status:"queued"},false,false);assert.equal(queued.badge,"本目标尚未启动");assert.match(queued.hint,/会开始本任务/);
  assert.deepEqual(plain(ui.goalChatNotes({status:"completed"},true,true)),{badge:"还没有该成员的执行记录",hint:"该成员在这个目标中还没有执行记录。"});
  assert.match(ui.goalChatNotes({status:"running"},true,false).hint,/会启动该成员/);
});

test("a paused task does not lock the person's independent general conversation",()=>{
  const state=fixture();state.goals=[{id:"goal",status:"paused"}];const {ui}=harness(state),person={active_goal_id:"goal",web_chat:true,running:false,where:"offline"};assert.equal(ui.chatAccess(person,null).readonly,false);assert.equal(ui.chatAccess(person,state.goals[0]).readonly,true);state.goals[0].status="completed";assert.equal(ui.chatAccess(person,null).readonly,false);
});

test("existing session model menus use its own tool catalog and never expose runtime conversion", () => {
  const {ui} = harness();
  const codex = ui.modelMenu(session());
  assert(codex.includes('data-model="gpt-a"'));
  assert(!codex.includes('data-model="claude-sonnet"'));
  assert(!codex.includes('data-act="leader-runtime"'));
  assert(codex.includes('data-effort="medium"'));
  assert(!codex.includes('data-effort="max"'));
  const claude = ui.modelMenu(session({runtime:"claude", model:"claude-sonnet", applied:null}));
  assert(claude.includes('data-model="claude-sonnet"'));
  assert(!claude.includes('data-model="gpt-a"'));
  assert.equal(ui.optionsLabel(session()), "GPT A · medium");
});

test("old states and sessions retain Claude defaults and Haiku effort behavior", () => {
  const state = fixture(); delete state.runtimes;
  const {ui} = harness(state);
  assert.equal(ui.runtimeInfo({}).id, "claude");
  assert(ui.modelMenu({session_id:"old", model:null, effort:null, applied:null}).includes('data-model="claude-sonnet"'));
  assert.equal(ui.modelEfforts("claude", "claude-haiku-4-5-20251001").length, 0);
  assert.equal(ui.runtimeInfo("codex").available, false);
});

test("Codex editor handoff is disabled while leader chat and member history remain available", () => {
  const state = fixture(), leader = session({session_id:"lead", name:"Codex leader", priority:null, member_ids:["member"]});
  const member = session({session_id:"member", name:"Codex member", leader_session_id:"lead", status:"idle", web_chat:true});
  state.leaders = [leader]; state.members = [member];
  const {ui} = harness(state), reason = "Codex 会话在 Crew 内继续；暂不支持转到 VS Code";
  for (const item of [leader, member]) {
    const markup = ui.sessionMenu(item);
    assert.equal(ui.editorHandoffReason(item), reason);
    assert.match(markup, new RegExp('data-act="open-vscode"[^>]*disabled title="' + reason + '"'));
    assert(markup.includes('<p class="editor-handoff-note">' + reason + '</p>'));
  }
  assert.match(ui.sessionMenu(leader), /data-act="leader"[^>]*>打开对话/);
  assert.match(ui.sessionMenu(member), /data-act="member-history"[^>]*>进入本人对话/);
  assert.equal(ui.editorHandoffReason({runtime:"claude"}), "");
  const claude = {...member, runtime:"claude"};
  assert.match(ui.sessionMenu(claude), /data-act="open-vscode"[^>]*>在 VS Code 打开/);
  assert(!ui.sessionMenu(claude).includes(reason));
});

test("an unavailable tool has a clear error and cannot submit or update options", () => {
  const state = fixture(); state.runtimes[1].available = false; state.runtimes[1].error = "未找到 Codex CLI，请先安装";
  const {ui} = harness(state);
  assert.throws(() => ui.optionChange(session(), "model", "gpt-a"), /未找到 Codex/);
  assert(ui.modelMenu(session()).includes('data-model="gpt-a" disabled'));
});

test("explicit catalog refresh is deduplicated and old servers are never probed", async () => {
  let requests = 0, resolve;
  const response = new Promise(done => { resolve = done; });
  const {ui} = harness(fixture(), url => {requests += 1; assert.equal(url, "/api/runtimes?project=fixture"); return response;});
  assert.equal(requests, 0);
  const first = ui.refreshRuntimes(), second = ui.refreshRuntimes();
  await Promise.resolve();
  assert.equal(requests, 1);
  const updated = fixture(); updated.runtimes[1].models = [{id:"new-codex-model", name:"New", efforts:["high"]}];
  resolve({ok:true, json:async () => ({runtimes:updated.runtimes})});
  await Promise.all([first, second]);
  assert(ui.modelMenu(session()).includes('data-model="new-codex-model"'));
  assert(!ui.modelMenu(session()).includes('data-model="gpt-a"'));
  const old = fixture(); delete old.runtimes;
  const legacy = harness(old, () => {throw new Error("Old server must not be probed");});
  await legacy.ui.refreshRuntimes();
});

const todo = extra => ({id:17, title:"在手机上确认登录", steps:"打开设置\n允许登录", verification:"告诉它已经授权",
  blocking:true, status:"open", note:"", session_id:"member-1", proposer_name:"连接助手", group:"登录",
  leader_session_id:"leader-1", leader_name:"登录", priority:1, created_at:"2026-10-24T00:00:00Z", updated_at:"2026-10-24T00:00:00Z", version:2, ...extra});
const todoState = () => ({...fixture(), leaders:[{session_id:"leader-1", name:"登录"}], members:[],
  decisions:[{id:90, leader_session_id:"leader-1", held:false}], todos:[todo({id:44}), todo({id:17}), todo({id:52, leader_session_id:"leader-2"})]});

test("first open prioritizes personal actions while explicit routes and old default remain stable", () => {
  const state = todoState(), {ui, location} = harness(state);
  assert.equal(ui.route().view, "todos");
  location.hash = "#leader=leader-1";
  assert.equal(ui.route().view, "chat");
  location.hash = "#all";
  assert.equal(ui.route().view, "all");
  state.todos = [];
  location.hash = "";
  assert.equal(ui.route().leaderId, "leader-1");
  assert.equal(ui.route().view, "chat");
  location.hash = "#todos";
  assert.equal(ui.route().view, "todos");
  delete state.todos;
  location.hash = "";
  assert.equal(ui.route().view, "chat");
});

test("personal action order comes from server and group trays and decisions have separate counts", () => {
  const {ui} = harness(todoState());
  assert.deepEqual(plain(ui.openTodos().map(item => item.id)), [44, 17, 52]);
  const group = ui.todosOf("leader-1");
  assert.deepEqual(plain(group.map(item => item.id)), [44, 17]);
  assert.equal(ui.pendingOf("leader-1").length, 1);
  const tray = ui.todoTray(group, "leader-1");
  assert(tray.includes('class="todo-tray"'));
  assert(tray.includes('待你动手 <span>2</span>'));
  assert(!tray.includes("等你拍板"));
  assert(tray.indexOf('data-todo-id="44"') < tray.indexOf('data-todo-id="17"'));
});

test("personal action page renders server order and a useful empty state", () => {
  const state = todoState(), {ui, nodes} = harness(state);
  ui.renderTodos();
  const page = nodes.get("main").innerHTML;
  assert(page.includes("我的待办"));
  assert(page.includes('class="todo-page-count">3</span>'));
  assert(page.indexOf('data-todo-id="44"') < page.indexOf('data-todo-id="17"'));
  assert(page.indexOf('data-todo-id="17"') < page.indexOf('data-todo-id="52"'));
  assert(!page.includes("等你拍板"));
  state.todos = [];
  ui.renderTodos();
  assert(nodes.get("main").innerHTML.includes("现在没有待你动手的事"));
});

test("personal action cards escape titles and show source, blocking, expandable steps and verification", () => {
  const state = todoState(), {ui} = harness(state);
  const item = todo({title:"<script>bad</script>", id:44});
  const collapsed = ui.todoCard(item);
  assert(collapsed.includes("&lt;script&gt;bad&lt;/script&gt;"));
  assert(!collapsed.includes("<script>"));
  assert(collapsed.includes("登录 · 连接助手"));
  assert(collapsed.includes("卡着 连接助手"));
  assert(collapsed.includes('id="todo-details-44" hidden'));
  ui.toggleTodo("44");
  const expanded = ui.todoCard(item);
  assert(!expanded.includes('id="todo-details-44" hidden'));
  assert(expanded.includes("打开设置"));
  assert(expanded.includes("告诉它已经授权"));
  assert(expanded.includes('data-act="todo-ask"'));
  assert(!expanded.includes("#44"));
});

test("completion note is optional, cancellation requires a reason, and shown version is preserved", () => {
  const {ui} = harness(todoState()), item = todo({version:5});
  assert.deepEqual(plain(ui.todoResolvePayload(item, {status:"done", note:"  ", version:2})),
    {id:17, status:"done", note:"", expected_version:2});
  assert.throws(() => ui.todoResolvePayload(item, {status:"cancelled", note:"  ", version:2}), /原因/);
  assert.deepEqual(plain(ui.todoResolvePayload(item, {status:"cancelled", note:" 已换方案 ", version:2})),
    {id:17, status:"cancelled", note:"已换方案", expected_version:2});
});

test("resolving a personal action posts its version and undo uses the exact server receipt", async () => {
  const state = todoState(), {ui} = harness(state), calls = [], notices = [];
  const receipt = {ok:true, before:{status:"open", note:""}, version:3};
  ui.startTodoAction("44","done");ui.setTodoNote("44"," 已授权 ");
  ui.services({api:async (url, body) => {calls.push([url, plain(body)]);return receipt;},
    loadState:async () => {state.todos = state.todos.filter(item => item.id !== 44);},
    toast:(message, undo) => notices.push({message, undo})});
  await ui.resolveTodo("44");
  assert.deepEqual(calls[0], ["/api/todo/resolve", {id:44, status:"done", note:"已授权", expected_version:2}]);
  assert.equal(ui.todoState("44").draft, null);
  assert.equal(typeof notices[0].undo, "function");
  await notices[0].undo();
  assert.deepEqual(calls[1], ["/api/todo/undo", {id:44, before:receipt.before, version:3}]);
});

test("a stale action retains the user's reason and asks them to review updated content", async () => {
  const state = todoState(), {ui} = harness(state), calls = [];
  state.todos.find(item=>item.id===44).version=1;
  ui.startTodoAction("44","cancelled");ui.setTodoNote("44","已经换方案");
  state.todos.find(item=>item.id===44).version=2;
  ui.services({api:async (url, body) => {calls.push(plain(body)); throw new Error("version conflict");},
    loadState:async () => {}, toast:() => {}});
  await ui.resolveTodo("44");
  assert.equal(calls[0].expected_version, 1);
  const draft=ui.todoState("44").draft;
  assert.equal(draft.note, "已经换方案");
  assert.equal(draft.version, 2);
  assert.match(draft.error, /内容已更新/);
  assert.equal(ui.todoState("44").expanded, true);
});

test("decision trust follows its explicit leader and ignores legacy project mode", () => {
  const state = todoState(); state.trust = {enabled:true, goal:"obsolete project goal"};
  state.leaders[0].trust = {enabled:false, goal:"leader one"};
  state.leaders.push({session_id:"leader-other", name:"Other", trust:{enabled:true, goal:"leader two"}});
  const {ui} = harness(state);
  assert.equal(ui.decisionTrust({leader_session_id:"leader-1"}).enabled, false);
  assert.equal(ui.decisionTrust({leader_session_id:"leader-other"}).goal, "leader two");
  assert.equal(ui.decisionTrust({session_id:"absent"}).enabled, false);
});

test("reports require an explicit current leader and scope the Markdown URL", async () => {
  const {ui} = harness(todoState(), async (url, options) => {
    assert.equal(url, "/api/trust/report?leader_session_id=leader-1&project=fixture");
    assert.equal(options.headers["X-Crew"], "1");
    return {ok:true, text:async () => "# 本组报告"};
  });
  assert.equal(await ui.readTrustReport("leader-1"), "# 本组报告");
  await assert.rejects(ui.readTrustReport(), /当前项目的 leader/);
  const error = harness(todoState(), async () => ({ok:false, text:async () => JSON.stringify({error:"leader不存在"})}));
  await assert.rejects(error.ui.readTrustReport("leader-1"), /leader不存在/);
});

const decision = extra => ({id:71, asker:"连接助手", session_id:"member-1", leader_session_id:"leader-1", presentation_stage:"ready",
  question:"是否继续连接？", options:["A. 继续连接", "B. 暂缓连接"], recommendation:"A", held:false,
  brief:{title:"是否允许连接测试设备", context:"technical-only-context", why_user:"设备需要使用你的个人账户。",
    recommendation_reason:"可以继续验证连接功能。", impact:"会在本机保存一次登录状态。",
    options:[{label:"允许使用这个账户", consequence:"继续连接验证，登录状态会保存在本机。"},
      {label:"暂缓连接", consequence:"暂停相关验证，先处理其他功能。"}]}, ...extra});

test("a decision first invites discussion and exposes structured choices only after ready", () => {
  const {ui}=harness(todoState());
  const notice=ui.decisionCard(decision({presentation_stage:undefined}));
  assert(notice.includes("这里需要你"));assert(notice.includes("听它说明"));assert(!notice.includes("answer-option"));assert(!notice.includes("账户"));
  const discussing=ui.decisionCard(decision({presentation_stage:"discussion"}));
  assert(discussing.includes("聊清楚了，给我选项"));assert(!discussing.includes("answer-option"));
  assert(ui.decisionCard(decision()).includes("answer-option"));
});

test("decision briefs show human context, rationale and independent consequences while preserving original choice indices", () => {
  const state = todoState(); state.leaders[0].priority = null;
  const {ui} = harness(state), item = decision(), original = item.options.slice();
  const card = ui.decisionCard(item), summary = card.split('<details')[0];
  assert(summary.includes("是否允许连接测试设备"));
  assert(summary.includes("设备需要使用你的个人账户。"));
  assert(summary.includes("可以继续验证连接功能。"));
  assert(summary.includes("会在本机保存一次登录状态。"));
  assert(summary.includes("继续连接验证，登录状态会保存在本机。"));
  assert(summary.includes("暂停相关验证，先处理其他功能。"));
  assert(!summary.includes("technical-only-context"));
  assert(!card.includes('data-act="decision-clarify"'));
  assert.deepEqual(plain(ui.decisionOptions(item).map(option => option.index)), [0, 1]);
  assert.deepEqual(item.options, original);
  assert(card.includes('data-act="answer-option" data-id="71" data-option="1"'));
  assert(card.includes('data-act="decision-hold"'));
  assert(!card.includes('data-decision-context="71" open'));
  ui.setDecisionOpen(71,true);
  assert(ui.decisionCard(item).includes('data-decision-context="71" open'));
});

test("legacy decisions expose missing explanations, compact the title and fold the full source without inventing reasons", () => {
  const state = todoState(); state.leaders[0].priority = null;
  const {ui} = harness(state), question = "原始技术描述".repeat(50) + "\n第二段技术资料";
  const card = ui.decisionCard(decision({brief:null, question})), visible = card.split('<details')[0];
  assert(visible.includes("缺少说明"));
  assert(visible.includes('data-act="decision-clarify"'));
  assert(visible.includes("尚未说明这个选择的结果"));
  assert(!visible.includes(question));
  assert(card.includes("查看原文和原选项"));
  assert(card.includes(question));
  assert(!visible.includes("推荐理由"));
  const noRecommendation = ui.decisionOptions(decision({brief:null, recommendation:undefined}));
  assert(noRecommendation.every(option => !option.recommended));
});

test("overlong structured explanations keep a compact preview and the exact full text in folded detail", () => {
  const state = todoState(); state.leaders[0].priority = null;
  const {ui} = harness(state), item = decision();
  const explanation = "完整而明确的说明。".repeat(80);
  item.brief.why_user = explanation;
  item.brief.options[0].consequence = explanation;
  const card = ui.decisionCard(item), visible = card.split('<details')[0];
  assert(!visible.includes(explanation));
  assert(visible.includes("…"));
  assert(card.includes("完整说明与技术背景"));
  assert(card.includes(explanation));
  assert(!card.includes('data-decision-context="71" open'));
});

test("live UI refresh preserves horizontal group scroll position", () => {
  const {ui} = harness(todoState());
  const before = {className:"lead-grid", scrollTop:12, scrollLeft:704};
  const after = {className:"lead-grid", scrollTop:0, scrollLeft:0};
  let replaced = false;
  const host = {dataset:{}, contains:() => false,
    querySelectorAll:selector => selector.startsWith(".page") ? [replaced ? after : before] : []};
  Object.defineProperty(host, "innerHTML", {set:() => {replaced = true;}});
  ui.replace(host, "updated group state");
  assert.equal(after.scrollLeft, 704);
  assert.equal(after.scrollTop, 12);
});

test("only a leader menu offers whole-department compaction, separate from compacting one session", () => {
  const state = fixture(), leader = session({session_id:"lead", name:"Leader", runtime:"claude", priority:null, member_ids:["member"]});
  const member = session({session_id:"member", name:"Member", runtime:"claude", leader_session_id:"lead", status:"idle", web_chat:true});
  state.leaders = [leader]; state.members = [member];
  const {ui} = harness(state), menu = ui.sessionMenu(leader);
  assert.match(menu, /data-act="compact-department" data-id="lead"[^>]*>压缩整个部门…/);
  assert.match(menu, /data-act="compact" data-id="lead"[^>]*>压缩上下文/);
  assert(!ui.sessionMenu(member).includes("compact-department"));
});

function contextState(runtime="codex") {
  const state=fixture(),lead=session({session_id:"lead",name:'Leader <&"',runtime,context:{used:12000,max:100000},
    status:"idle",busy:false,priority:null,goal:"goal",pending:0,unread:0,codex:[],member_ids:["member"]});
  const member={...lead,session_id:"member",name:"Member",leader_session_id:"lead",group:"Leader",task:"task"};
  return {...state,project:{name:"fixture"},leaders:[lead],members:[member],decisions:[]};
}

test("every context ring is a named native button at low and high usage across both carriers and roles",()=>{
  for(const runtime of ["claude","codex"])for(const used of [1000,49000,95000]){
    const state=contextState(runtime),{ui}=harness(state);
    for(const item of [...state.leaders,...state.members]){
      item.context.used=used;
      const html=ui.contextRing(item);
      assert.match(html,/^<button type="button" class="context-ring /);
      assert(html.includes(`data-act="context-compact" data-id="${item.session_id}"`));
      assert(html.includes("点击压缩上下文"));assert(!html.includes('role="img"'));
      assert(!html.includes(' disabled'));assert(!html.includes('Leader <&"'));
      assert.equal((ui.contextControls(item).match(/<button /g)||[]).length,1,"the ring replaces the duplicate text control");
    }
    assert(ui.sidebarLeader(state.leaders[0]).startsWith('<div draggable="true"'));
    assert(ui.memberNode(state.members[0]).includes('<div class="node session-surface'));
    assert(ui.workingRow(state.leaders[0]).includes('class="session-open"'));
  }
});

test("covered chat emits a reply cue while nonoverlapping auxiliary cards preserve active reading across carriers",()=>{
  const {create:notifications}=require("../crew/web/waiting-notifications.js"),{create:visibility}=require("../crew/web/ui-visibility.js");
  for(const runtime of ["claude","codex"])for(const [settings,watching] of [[{},true],[{tree:true},true],[{pending:true},true],[{tree:true,overlap:true},false],[{pending:true,overlap:true},false],[{tools:true},false],[{materials:true},false],[{reader:true},false],[{comments:true},false],[{creation:true},false],[{list:true},false],[{member:true},false]]){
    const played=[],visible=visibility();
    for(const owner of ["tools","materials","reader","comments","creation","list"])visible.register(owner,()=>!!settings[owner]);
    visible.register("auxiliary",()=>!!((settings.pending||settings.tree)&&settings.overlap));
    const cues=notifications({sound:{play:kind=>played.push(kind)},visibility:visible,document:{visibilityState:"visible",hasFocus:()=>true}});
    const baseline={leaders:[session({runtime,last_reply_id:null})],members:[],decisions:[],attention:[],notification:{channels:[{state:"connected"}],error:null}};
    const reading={view:"chat",leaderId:"session-codex",readOnlyId:settings.member?"member":null};
    cues.sync(baseline);cues.sync({...baseline,leaders:[session({runtime,last_reply_id:"new-reply"})]},{reading});
    assert.deepEqual(played,watching?[]:["reply"],JSON.stringify({runtime,settings}));
    played.length=0;cues.sync({...baseline,leaders:[session({runtime,last_reply_id:"different-leader-reply"})]},{reading:{...reading,leaderId:"another-leader"}});
    assert.deepEqual(played,["reply"]);cues.destroy();visible.destroy();
  }
});
