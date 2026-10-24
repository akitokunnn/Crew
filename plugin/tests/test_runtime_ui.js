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
const form = (runtime="codex", model="", effort="") => ({elements:{name:{value:" fixture "}, task:{value:" work "},
  runtime:{value:runtime}, model:{value:model}, effort:{value:effort}},
  submit:{disabled:false}, refresh:{disabled:false, hidden:false},
  querySelector(selector) { return selector === '[type="submit"]' ? this.submit : this.refresh; }});

function harness(state = fixture(), fetcher = () => { throw new Error("Unexpected network request"); }) {
  const nodes = new Map();
  const node = () => ({innerHTML:"", textContent:"", className:"", hidden:false, dataset:{},
    contains:() => false, querySelectorAll:() => [], setAttribute(name, value) { this[name] = value; }});
  for (const id of ["main", "trust-error", "leader-runtime-options", "leader-runtime-status", "leader-model-options", "leader-effort-note", "leader-effort-options"]) nodes.set(id, node());
  const escape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const sandbox = {window:{}, document:{activeElement:null, getElementById:id => nodes.get(id) || null, addEventListener:() => {}},
    location:{search:"?project=fixture", origin:"http://fixture.local", hash:""}, URLSearchParams, URL, fetch:fetcher, console,
    CrewChat:{renderInline:escape, renderMarkdown:text => `<p>${escape(text)}</p>`}};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web/leader-trust-ui.js"), "utf8"), sandbox);
  // Expose lexical UI functions while leaving setup and its state-poll timer off.
  const end = "  setup();\n})();";
  assert(source.endsWith(end + "\n") || source.endsWith(end));
  vm.runInContext(source.replace(end, `  window.ui = {runtimeList, runtimeInfo, modelEfforts, modelMenu, optionChange,
    optionsLabel, leaderPayload, renderLeaderRuntime, refreshRuntimes, editorHandoffReason, sessionMenu, chatAccess,
    openTodos, todosOf, pendingOf, todoCard, todoTray, todoExpanded, todoTrayExpanded, todoActions,
    todoResolvePayload, resolveTodo, renderTodos, replace,
    decisionTrust, trustReportUrl, readTrustReport,
    compactDecisionText, decisionOptions, decisionCard, decisionsOpen,
    commentButton, commentsMarkup, sessionComments, loadComments, postComment,
    route() {readRoute(); return {view,leaderId,readOnlyId};},
    services(values) {if(values.api)api=values.api;if(values.loadState)loadState=values.loadState;if(values.toast)toast=values.toast;},
    setState(value) {state=value;runtimesCache=null;}};\n})();`), sandbox);
  sandbox.window.ui.setState(state);
  return {ui:sandbox.window.ui, nodes, location:sandbox.location};
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

test("goal status controls composer access independently of the person's general chat history", () => {
  const {ui}=harness(),person={web_chat:false,running:true,where:"vscode"};
  for(const status of ["paused","pausing","completed","failed","cancelled","awaiting_acceptance"]){const access=ui.chatAccess(person,{status});assert.equal(access.readonly,true);assert.match(access.readonlyReason,/总对话可讨论；继续工作先恢复\/再开一轮/);}
  for(const status of ["queued","running","waiting_user"])assert.deepEqual(plain(ui.chatAccess(person,{status})),{readonly:false});
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

test("creation form resets foreign models and unsupported efforts on tool/model changes", () => {
  const {ui, nodes} = harness();
  const leader = form("codex", "claude-sonnet", "max"); nodes.set("leader-form", leader);
  ui.renderLeaderRuntime();
  assert.equal(leader.elements.model.value, "");
  assert(nodes.get("leader-model-options").innerHTML.includes('data-model="gpt-a"'));
  assert(!nodes.get("leader-model-options").innerHTML.includes('data-model="claude-sonnet"'));
  leader.elements.model.value = "gpt-b"; leader.elements.effort.value = "medium";
  ui.renderLeaderRuntime();
  assert.equal(leader.elements.effort.value, "");
  assert(nodes.get("leader-effort-options").innerHTML.includes('data-effort="high"'));
  assert(!nodes.get("leader-effort-options").innerHTML.includes('data-effort="medium"'));
  assert.deepEqual(plain(ui.leaderPayload(leader)), {name:"fixture", task:"work", runtime:"codex", model:"gpt-b", effort:null});
});

test("creation and options refuse a model or effort from the other tool", () => {
  const {ui} = harness();
  assert.throws(() => ui.leaderPayload(form("codex", "claude-sonnet")), /模型列表/);
  assert.throws(() => ui.leaderPayload(form("codex", "gpt-a", "max")), /不支持/);
  assert.throws(() => ui.optionChange(session(), "model", "claude-sonnet"), /模型列表/);
  assert.throws(() => ui.optionChange(session(), "effort", "max"), /不支持/);
  assert.deepEqual(plain(ui.optionChange(session({model:"gpt-b", effort:"max", applied:{model:"gpt-b", effort:"max"}}), "model", "gpt-a")), {model:"gpt-a", effort:"medium"});
  assert.deepEqual(plain(ui.optionChange(session(), "model", "gpt-b")), {model:"gpt-b", effort:"high"});
  assert.throws(() => ui.optionChange(session(), "model", "gpt-c"), /先停下/);
  assert.deepEqual(plain(ui.optionChange(session({where:"offline", running:false}), "model", "gpt-c")), {model:"gpt-c", effort:null});
  assert(!("runtime" in ui.optionChange(session(), "effort", "low")));
});

test("an unavailable tool has a clear error and cannot submit or update options", () => {
  const state = fixture(); state.runtimes[1].available = false; state.runtimes[1].error = "未找到 Codex CLI，请先安装";
  const {ui, nodes} = harness(state), leader = form(); nodes.set("leader-form", leader);
  ui.renderLeaderRuntime();
  assert.equal(leader.submit.disabled, true);
  assert.equal(nodes.get("leader-runtime-status").textContent, state.runtimes[1].error);
  assert.throws(() => ui.leaderPayload(leader), /未找到 Codex/);
  assert.throws(() => ui.optionChange(session(), "model", "gpt-a"), /未找到 Codex/);
  assert(ui.modelMenu(session()).includes('data-model="gpt-a" disabled'));
});

test("explicit catalog refresh is deduplicated and old servers are never probed", async () => {
  let requests = 0, resolve;
  const response = new Promise(done => { resolve = done; });
  const {ui} = harness(fixture(), url => {requests += 1; assert.equal(url, "/api/runtimes?project=fixture"); return response;});
  assert.equal(requests, 0);
  const first = ui.refreshRuntimes(), second = ui.refreshRuntimes();
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
  assert(tray.includes('class="todo-tray collapsed"'));
  assert(tray.includes("本组待你动手 · 2"));
  assert(!tray.includes("等你拍板"));
  ui.todoTrayExpanded.add("leader-1");
  assert(!ui.todoTray(group, "leader-1").includes('class="todo-tray collapsed"'));
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
  ui.todoExpanded.add("44");
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
  ui.todoActions.set("44", {status:"done", note:" 已授权 ", version:2});
  ui.services({api:async (url, body) => {calls.push([url, plain(body)]);return receipt;},
    loadState:async () => {state.todos = state.todos.filter(item => item.id !== 44);},
    toast:(message, undo) => notices.push({message, undo})});
  await ui.resolveTodo("44");
  assert.deepEqual(calls[0], ["/api/todo/resolve", {id:44, status:"done", note:"已授权", expected_version:2}]);
  assert.equal(ui.todoActions.has("44"), false);
  assert.equal(typeof notices[0].undo, "function");
  await notices[0].undo();
  assert.deepEqual(calls[1], ["/api/todo/undo", {id:44, before:receipt.before, version:3}]);
});

test("a stale action retains the user's reason and asks them to review updated content", async () => {
  const state = todoState(), {ui} = harness(state), calls = [];
  const draft = {status:"cancelled", note:"已经换方案", version:1};
  ui.todoActions.set("44", draft);
  ui.services({api:async (url, body) => {calls.push(plain(body)); throw new Error("version conflict");},
    loadState:async () => {}, toast:() => {}});
  await ui.resolveTodo("44");
  assert.equal(calls[0].expected_version, 1);
  assert.equal(draft.note, "已经换方案");
  assert.equal(draft.version, 2);
  assert.match(draft.error, /内容已更新/);
  assert.equal(ui.todoActions.get("44"), draft);
  assert.equal(ui.todoExpanded.has("44"), true);
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
  ui.decisionsOpen.add(71);
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

test("group and member comment entries target the exact session and render readable escaped comments", () => {
  const state = todoState(); state.members = [{session_id:"member-1", name:"连接助手", leader_session_id:"leader-1"}];
  const {ui} = harness(state), member = state.members[0];
  assert(ui.commentButton(state.leaders[0], true).includes('data-id="leader-1"'));
  assert(ui.commentButton(member).includes('data-id="member-1"'));
  const body = ui.commentsMarkup(member, {comments:[{text:"<script>comment</script>", created_at:"2026-10-24T00:00:00Z"}], draft:"待补充的要求"});
  assert(body.includes("登录 组 · 评论会转告 leader"));
  assert(body.includes("&lt;script&gt;comment&lt;/script&gt;"));
  assert(!body.includes("<script>"));
  assert(body.includes("待补充的要求"));
});

test("comment writes preserve failed drafts, then refresh the same session and clear successful text", async () => {
  const state = todoState(), {ui, nodes} = harness(state), calls = [];
  let fail = true;
  const textarea = {value:"保留这段反馈"};
  nodes.set("comments-form", {dataset:{sessionId:"leader-1"}, elements:{text:textarea}});
  ui.services({api:async (url, body) => {
    calls.push([url, body && plain(body)]);
    if (body && fail) throw new Error("暂时无法发送");
    return body ? {id:1} : {comments:[{id:1, text:"保留这段反馈", created_at:"2026-10-24T00:00:00Z"}]};
  }, toast:() => {}});
  await ui.postComment("leader-1", "保留这段反馈");
  assert.equal(ui.sessionComments("leader-1").draft, "保留这段反馈");
  assert.equal(ui.sessionComments("leader-1").error, "暂时无法发送");
  fail = false;
  await ui.postComment("leader-1", " 保留这段反馈 ");
  assert.deepEqual(calls[1], ["/api/comments", {session_id:"leader-1", text:"保留这段反馈"}]);
  assert.equal(calls[2][0], "/api/comments?session_id=leader-1");
  assert.equal(ui.sessionComments("leader-1").comments.length, 1);
  assert.equal(ui.sessionComments("leader-1").draft, "");
  assert.equal(textarea.value, "");
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
