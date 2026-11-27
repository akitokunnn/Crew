"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const tick = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
const plain = value => JSON.parse(JSON.stringify(value));
const body = (assignment, questions) => `# 切换签名\n\n用统一的 v2。\n\n## 分工\n- [A1] 支付 | ${assignment}\n\n## 等你定\n${questions}\n\n## 完成标准\n- [C1] 全部回归通过\n`;
const plan1 = {version:1, body:body("整理方案", "- [Q1] 用什么算法 | HMAC\n- [Q2] 何时上线 | 周五"), summary:"第一份", author:"assistant",
  created_at:"2026-11-28T02:01:00Z", budget_tokens:null};
const plan2 = {...plan1, version:2, body:body("切换网关", "- [Q1] 用什么算法 | HMAC-SHA256\n- [Q2] 何时上线 | 周五 | 下周一"), summary:"补充网关", created_at:"2026-11-28T02:02:00Z", budget_tokens:500000};
const plan3 = {...plan2, version:3, body:plan2.body.replace("切换网关", "逐步切换网关\n- [A2] 支付 | 观察错误率"),
  summary:"分批切换", created_at:"2026-11-28T02:04:00Z"};
const message = (id, speaker_session_id, body, extra = {}) => ({id, speaker_session_id, body, speaker_name:speaker_session_id,
  created_at:"2026-11-28T02:03:00Z", mentions:[], notified:[], reply:null, ...extra});
const job = (id, kind, status, title) => ({entry_id:id, kind, status, title, owner_session_id:"pay", owner_name:"支付", note:""});
function room(extra = {}) {
  return {id:3, status:"open", phase:"preparing", draft_version:2, approved_version:null, legacy:false, close_reason:null, title:"签名",
    goal:"统一 v2", lead_session_id:"assistant", lead_name:"工作区助手", budget:null, messages:[message(1,"assistant","先写计划")],
    decisions:[], consults:[], plans:[plan2, plan1], board:[], events:[], has_more:false, origin:null, read_id:1, latest_id:1, unread:0, open_decisions:0, members:[], summary:null, ...extra};
}
function element() {
  const classes = new Set();
  const node = {innerHTML:"", textContent:"", disabled:false, hidden:false, open:false, value:"", dataset:{}, scrollHeight:100, clientHeight:100, scrollTop:0,
    focus(){ this.focused = true; }, scrollIntoView(){ this.scrolled = true; }, showModal(){ this.open = true; }, close(){ this.open = false; this.onclose?.(); },
    replaceChildren(){ this.innerHTML = ""; },
    hasAttribute(name){ return name === "data-mark" && Object.hasOwn(this.dataset, "mark"); },
    classList:{toggle(name, active){ active ? classes.add(name) : classes.delete(name); }, add:name => classes.add(name),
      remove:name => classes.delete(name), contains:name => classes.has(name)}, querySelectorAll:() => [], querySelector:() => null};
  return node;
}
function harness(initial, {mobile = false, reads, mutation, directory = null} = {}) {
  const calls = [], toasts = [], routes = [], nodes = new Map(), listeners = new Set();
  const state = {project:{name:"A"}, leaders:[{session_id:"pay", name:"支付"}]};
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, element());
    return nodes.get(key);
  };
  const host = {...element(), querySelector:node};
  const document = {body:{classList:{contains:() => false}}, visibilityState:"visible", querySelector:() => null,
    addEventListener:(_, callback) => listeners.add(callback), removeEventListener:(_, callback) => listeners.delete(callback)};
  const sandbox = {document, URL, URLSearchParams, atob, btoa, TextDecoder, location:{pathname:"/", search:""}, matchMedia:() => ({matches:mobile}),
    CrewChat:{renderMarkdown(text){ return sandbox.CrewReportMarkdown.render(text); }}, CrewBrowser:{
      pageProject:() => ({mode:"workspace", project:"A"}), enableChatLists(){}, chatSendKey:event => event.key === "Enter" && !event.isComposing
    }};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const file of ["vendor/markdown-it.min.js","report-markdown.js","global-rail.js","channel.js","war-room-plan.js","war-room.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8"), sandbox, {filename:file});
  }
  const W = sandbox.CrewWarRoom;
  const form = node(".wr-confirm form"), submit = element();
  form.elements = {title:{value:"签名"}, goal:{value:"统一"}, lead:{value:"pay"}, summary:{value:"已交接"}, budget:{value:"900000"}};
  form.querySelector = selector => selector === ".wr-dialog-error" ? node("confirmation error") : submit;
  node(".wr-pane-dialog").querySelector = () => node("panel close");
  node(".wr-send").form = node("send form");
  let detail = initial;
  const api = async (route, data) => {
    const name = route.split(".").slice(1).join(".").split("?")[0];
    calls.push({name, data:plain(data ?? null)});
    if (name === "get") return plain(reads ? await reads(detail) : detail);
    if (name === "mark_read") return {read_id:data.id};
    if (mutation) return mutation(name, data, value => { detail = value; });
    return {};
  };
  W.mount({host, id:directory === null ? 3 : 0, rooms:directory, roomsError:"", state, api, toast:text => toasts.push(text),
    openRoom:id => routes.push(id), refreshRooms(){}, onChanged(){}, openChannel(){}, avatar:() => ""});
  const click = (wr, attrs = {}) => host.onclick({target:{closest:selector => selector === "button[data-wr]" ? {dataset:{wr,...attrs}} : null}});
  const send = async () => { node("send form").onsubmit({preventDefault(){}}); await tick(); };
  const confirm = async () => { form.onsubmit({preventDefault(){}}); await tick(); };
  return {W, P:sandbox.CrewWarRoomPlan, rail:sandbox.CrewGlobalRail, calls, node, host, toasts, routes, listeners, click, send, confirm,
    reload(value) { detail = value; W.changed([{type:"war_room.updated", entity:"3"}]); }};
}

function watchPlan(f, panel, keys) {
  const blocks = keys.map(key => {
    const block = element();
    block.dataset.wrBlock = key;
    return block;
  });
  const scroll = element();
  scroll.scrollTop = 120;
  const document = {querySelectorAll:() => blocks};
  panel.querySelector = selector => selector === ".wr-plan-document" ? document : selector === ".wr-panel-scroll" ? scroll : null;
  f.host.querySelectorAll = selector => selector === ".wr-plan-document [data-wr-block]" ? blocks : [];
  return {blocks, scroll};
}

test("筹备页显示当前决定和已定折叠，确认期间看到的推荐值、预算和版本保持绑定", async () => {
  const f = harness(room(), {mutation(name, data, setRoom) {
    assert.equal(name, "plan.approve");
    setRoom(room({phase:"active", approved_version:2, draft_version:2,
      board:[job("A1","assignment","doing","切换网关"), job("C1","criterion","todo","全部回归通过")]}));
    return {};
  }});
  await tick();
  const aside = f.node(".wr-plan-panel");
  assert.match(aside.innerHTML, /作战计划书 <span>v2/);
  assert.match(aside.innerHTML, /Q1[\s\S]*领头建议 HMAC-SHA256[\s\S]*引用回答/);
  assert.match(aside.innerHTML, /✓ 已定 \(1\)/);
  assert.match(aside.innerHTML, /何时上线 · 下周一/);
  f.click("compare");
  assert.match(aside.innerHTML, /展开上一版全文/);
  assert.match(f.node(".wr-timeline").innerHTML, /data-block="budget"/);
  f.click("approve", {version:"2"});
  assert.match(f.node(".wr-confirm form").innerHTML, /还有 1 项没定，按领头推荐直接开打/);
  assert.equal(f.calls.filter(call => call.name === "plan.approve").length, 0);
  await f.confirm();
  assert.deepEqual(f.calls.find(call => call.name === "plan.approve").data,
    {room:3, version:2, recommendations:{Q1:"HMAC-SHA256"}, budget_tokens:500000});
  assert.equal(f.host.dataset.phase, "active");
  assert.match(aside.innerHTML, /分工进度 <span>0\/1/);
  assert.match(aside.innerHTML, /在做/);
  assert.doesNotMatch(aside.innerHTML, /需要你关注|全部回归通过/);
  f.click("collapse");
  assert.equal(f.host.dataset.collapsed, "true");
  f.click("full");
  assert.match(f.node(".wr-pane-content").innerHTML, /作战计划书/);
  assert.doesNotMatch(f.node(".wr-pane-content").innerHTML, /敲定，开打/);
  f.W.unmount();
  assert.equal(f.listeners.size, 0);
});

test("没有待定项时直接批准，连续点击只发一次写请求", async () => {
  let release;
  const latest = {...plan2, body:body("切换网关", "")};
  const f = harness(room({plans:[latest,plan1]}), {mutation:() => new Promise(resolve => { release = resolve; })});
  await tick();
  f.click("approve",{version:"2"}); f.click("approve",{version:"2"});
  assert.equal(f.node(".wr-confirm").open,false);
  assert.deepEqual(f.calls.filter(call => call.name === "plan.approve").map(call => call.data.recommendations),[{}]);
  release({});
  await tick();
});

test("版本冲突保留所见版本，不会偷偷批准刷新后的计划；另外三种错误显示具体原因和行号", async () => {
  for (const [code, detail, expected] of [
    ["version_conflict", {}, /计划变了/],
    ["missing_recommendation", {}, /没有推荐值/],
    ["invalid_criteria", {line:11}, /完成标准没写对（第 11 行）/],
    ["invalid_plan", {line:5}, /计划书有一处没写对（第 5 行）/]
  ]) {
    const f = harness(room(), {mutation:() => { throw Object.assign(Error("服务端原因"), {details:{code, detail}}); }});
    await tick();
    f.click("approve",{version:"2"});
    const update = {...plan2, version:3, body:body("改网关", "- [Q1] 用什么算法 | RSA")};
    f.reload(room({plans:[update, plan2, plan1], draft_version:3}));
    await tick();
    await f.confirm();
    const attempts = f.calls.filter(call => call.name === "plan.approve");
    assert.equal(attempts.length,1);
    assert.equal(attempts[0].data.version,2);
    assert.equal(attempts[0].data.recommendations.Q1,"HMAC-SHA256");
    assert.match(f.toasts.at(-1), expected);
    assert.match(f.node(".wr-plan-panel").innerHTML, /v3/);
    assert.equal(f.node(".wr-confirm").open,false);
  }
});

test("版本卡定位相邻稿的变动，引用发送成功以后才淡出；工作区助手也直接收到", async () => {
  let tries = 0;
  const f = harness(room(), {mutation(name, data, setRoom) {
    assert.equal(name,"post");
    if (++tries === 1) throw Error("没有送到，请重试");
    const post = message(2,"user",data.body);
    setRoom(room({messages:[message(1,"assistant","先写计划"),post]}));
    return {message:post};
  }});
  const mark = element();
  mark.dataset.wrBlock = "entry:A1";
  const document = {querySelectorAll:() => [mark]};
  f.node(".wr-plan-panel").querySelector = selector => selector === ".wr-plan-document" ? document : null;
  f.host.querySelectorAll = selector => selector.includes("data-wr-block") ? [mark] : [];
  await tick();
  assert.match(f.node(".wr-timeline").innerHTML, /v1 → v2[\s\S]*data-block="entry:A1"/);
  assert.equal(mark.dataset.mark,"改");
  f.click("jump",{version:"2",block:"entry:A1"});
  assert(mark.scrolled && mark.classList.contains("wr-flash"));
  f.node("textarea").value = "用领头的推荐";
  f.click("reference",{entry:"Q1"});
  assert.equal(f.node("textarea").value,"关于 Q1：用领头的推荐");
  await f.send();
  assert.equal(f.node("textarea").value,"关于 Q1：用领头的推荐");
  assert.equal(mark.classList.contains("wr-mark-faded"),false);
  await f.send();
  assert.equal(f.node("textarea").value,"");
  assert.equal(mark.classList.contains("wr-mark-faded"),true);
  assert.deepEqual(f.calls.filter(call => call.name === "post")[1].data,
    {room:3, body:"关于 Q1：用领头的推荐", to:["assistant"]});
  assert.match(f.node(".wr-timeline").innerHTML, /关于 Q1：用领头的推荐/);
});

test("作战中收到 v3 修订版，对话显示锚点，全文标出新改，发言成功后淡出", async () => {
  const revised = room({phase:"active", approved_version:2, draft_version:3, plans:[plan3, plan2, plan1]});
  const f = harness(room({phase:"active", approved_version:2}), {mutation(name, data, setRoom) {
    assert.equal(name, "post");
    const post = message(2, "user", data.body, {created_at:"2026-11-28T02:05:00Z"});
    setRoom({...revised, messages:[...revised.messages, post]});
    return {message:post};
  }});
  await tick();
  f.reload(revised);
  await tick();
  assert.match(f.node(".wr-timeline").innerHTML, /v2 → v3[\s\S]*data-block="entry:A1"><span>改<\/span>[\s\S]*data-block="entry:A2"><span>新<\/span>/);
  const pane = f.node(".wr-pane-content");
  const {blocks} = watchPlan(f, pane, ["entry:A1", "entry:A2"]);
  f.click("full");
  assert.match(pane.innerHTML, /作战计划书 <span>v3/);
  assert.match(pane.innerHTML, /逐步切换网关[\s\S]*观察错误率/);
  assert.match(pane.innerHTML, /敲定修订/);
  assert.deepEqual(blocks.map(block => block.dataset.mark), ["改", "新"]);
  assert(blocks.every(block => !block.classList.contains("wr-mark-faded")));
  f.click("hide-panel");
  f.node("textarea").value = "收到这版";
  await f.send();
  f.click("full");
  assert(blocks.every(block => block.classList.contains("wr-mark-faded")));
  assert.match(f.node(".wr-timeline").innerHTML, /v2 → v3/);
});

test("v2 的历史锚点在作战中打开 v3，跳到改过的小节中同 ID 的条目", async () => {
  const moved = {...plan3, body:plan3.body.replace("- [A1] 支付 | 逐步切换网关\n- [A2] 支付 | 观察错误率",
    "- [A2] 支付 | 观察错误率\n- [A1] 支付 | 逐步切换网关")};
  const f = harness(room({phase:"active", approved_version:2, draft_version:3, plans:[moved, plan2, plan1]}));
  await tick();
  const pane = f.node(".wr-pane-content");
  const {blocks:[other, target]} = watchPlan(f, pane, ["entry:A2", "entry:A1"]);
  f.click("jump", {version:"2", block:"entry:A1"});
  assert.equal(f.node(".wr-pane-dialog").open, true);
  assert.match(pane.innerHTML, /作战计划书 <span>v3[\s\S]*观察错误率[\s\S]*逐步切换网关/);
  assert(target.scrolled && target.classList.contains("wr-flash"));
  assert.equal(other.scrolled, undefined);
});

test("旧正文锚点能跟随段落移动，同序号的另一段不会冒充已删除的段落", async () => {
  const original = {...plan2, body:plan2.body.replace("用统一的 v2。", "核对发布窗口。")};
  const moved = {...plan3, body:plan3.body.replace("用统一的 v2。", "先看回滚条件。\n\n核对发布窗口。")};
  const removed = {...moved, version:4, body:moved.body.replace("\n\n核对发布窗口。", ""), created_at:"2026-11-28T02:05:00Z"};
  const f = harness(room({draft_version:3, plans:[moved, original, plan1]}));
  await tick();
  const key = '["切换签名","paragraph_open"]:1';
  const pane = f.node(".wr-plan-panel");
  const {blocks:[other, target]} = watchPlan(f, pane, [key, '["切换签名","paragraph_open"]:2']);
  assert.match(f.node(".wr-timeline").innerHTML, /v1 → v2[\s\S]*改<\/span> 核对发布窗口。/);
  f.click("jump", {version:"2", block:key});
  assert.equal(other.scrolled, undefined);
  assert(target.scrolled && target.classList.contains("wr-flash"));

  f.reload(room({draft_version:4, plans:[removed, moved, original, plan1]}));
  await tick();
  f.click("jump", {version:"2", block:key});
  assert.match(pane.innerHTML, /作战计划书 <span>v4/);
  assert.deepEqual(f.toasts, ["这一段在 v4 已删除"]);
  assert.equal(other.scrolled, undefined);
});

test("历史锚点已删除时打开最新版并提示首次消失的版本，不滚动计划书", async () => {
  const deleted = {...plan3, body:plan3.body.replace("- [A1] 支付 | 逐步切换网关\n", "")};
  const v4 = {...deleted, version:4, created_at:"2026-11-28T02:05:00Z"};
  for (const plans of [[deleted, plan2, plan1], [v4, deleted, plan2, plan1]]) {
    const version = plans[0].version;
    const f = harness(room({draft_version:version, plans}));
    await tick();
    const pane = f.node(".wr-plan-panel");
    const {blocks:[remaining], scroll} = watchPlan(f, pane, ["entry:A2"]);
    f.click("jump", {version:"2", block:"entry:A1"});
    assert.match(pane.innerHTML, new RegExp(`作战计划书 <span>v${version}`));
    assert.deepEqual(f.toasts, ["这一段在 v3 已删除"]);
    assert.equal(scroll.scrollTop, 120);
    assert.equal(remaining.scrolled, undefined);
    assert(!remaining.classList.contains("wr-flash"));
  }
});

test("删条目优先跳到最新版原小节，小节后来删除或当版已删时提示具体版本", async () => {
  const heading = '["等你定","heading_open"]:1';
  const v3 = {...plan3, body:plan3.body.replace("- [Q2] 何时上线 | 周五 | 下周一\n", "")};
  const v4 = {...v3, version:4, body:v3.body.replace(/## 等你定\n[^\n]+\n\n/, ""), created_at:"2026-11-28T02:05:00Z"};
  const f = harness(room({draft_version:3, plans:[v3, plan2, plan1]}), {mobile:true});
  await tick();
  const pane = f.node(".wr-pane-content");
  const {blocks:[section]} = watchPlan(f, pane, [heading]);
  assert.match(f.node(".wr-timeline").innerHTML, /data-block="[^"]*等你定[^"]*"><span>删<\/span> Q2/);
  f.click("jump", {version:"3", block:heading});
  assert(section.scrolled && section.classList.contains("wr-flash"));
  f.click("hide-panel");
  f.reload(room({draft_version:4, plans:[v4, v3, plan2, plan1]}));
  await tick();
  const {blocks:[remaining], scroll} = watchPlan(f, pane, ["entry:A2"]);
  f.click("jump", {version:"3", block:heading});
  f.click("hide-panel");
  f.click("jump", {version:"4", block:heading});
  assert.deepEqual(f.toasts, ["这一段在 v4 已删除", "这一段在 v4 已删除"]);
  assert.equal(scroll.scrollTop, 120);
  assert.equal(remaining.scrolled, undefined);
});

test("没有所属小节的删条目提示当版已删，仍可打开最新版", async () => {
  const introduced = {...plan2, body:`临时说明\n\n${plan2.body}`};
  const f = harness(room({draft_version:3, plans:[plan3, introduced, plan1]}));
  await tick();
  const pane = f.node(".wr-plan-panel");
  const {scroll} = watchPlan(f, pane, ["entry:A1"]);
  assert.match(f.node(".wr-timeline").innerHTML, /data-version="3" data-block=""><span>删<\/span> 临时说明/);
  f.click("jump", {version:"3", block:""});
  assert.match(pane.innerHTML, /作战计划书 <span>v3/);
  assert.deepEqual(f.toasts, ["这一段在 v3 已删除"]);
  assert.equal(scroll.scrollTop, 120);
});

test("没有条目的历史锚点卡仍用「看这一版」打开当时的计划书", async () => {
  const unchanged = {...plan1, version:2, created_at:plan2.created_at};
  const f = harness(room({phase:"active", draft_version:3, plans:[plan3, unchanged, plan1]}));
  await tick();
  assert.match(f.node(".wr-timeline").innerHTML, /data-version="2">看这一版/);
  f.click("jump", {version:"2"});
  const pane = f.node(".wr-pane-content").innerHTML;
  assert.match(pane, /作战计划书 <span>v2[\s\S]*整理方案/);
  assert.doesNotMatch(pane, /逐步切换网关/);
  assert.deepEqual(f.toasts, []);
});

test("手机摘要打开同一份计划并在单内敲定，正常发言和回复都发给领头会话", async () => {
  const f = harness(room(), {mobile:true, mutation:(_, data) => ({message:message(2,"user",data.body)})});
  await tick();
  assert.match(f.node(".wr-panelbar").innerHTML, /计划书 v2 · 1 项等你定/);
  f.click("panel");
  assert.equal(f.node(".wr-pane-dialog").open,true);
  assert.match(f.node(".wr-pane-content").innerHTML, /敲定，开打/);
  f.click("hide-panel");
  f.click("reply",{message:"1"});
  f.node("textarea").value = "确认";
  await f.send();
  assert.deepEqual(f.calls.find(call => call.name === "post").data,{room:3,body:"确认",to:["assistant"],reply_to:1});
});

test("作战中战况显示卡住和待定项，成员和内部消息在独立抽屉，顾问用量未知时有明确提示", async () => {
  const asked = {id:8, status:"open", question:"先做哪项？", recommendation:"网关", answer:null, created_at:"2026-11-28T02:05:00Z"};
  const f = harness(room({phase:"active", approved_version:2, open_decisions:1, decisions:[asked],
    board:[job("A1","assignment","blocked","切换网关")], members:[{session_id:"pay",crew_name:"支付"}],
    messages:[message(1,"assistant","先写计划"),message(2,"pay","内部讨论")],
    consults:[{question_id:"g", purpose:"creative", runtime:"codex", model:"实际模型", effort:"high", tokens:null,
      question:"能否简化？", response:"可以", created_at:"2026-11-28T02:04:00Z"}, {
      question_id:"h", purpose:"routine", runtime:"claude", model:"实际模型二", effort:"low", tokens:0,
      question:"有没有遗漏？", response:"无", created_at:"2026-11-28T02:04:15Z"}],
    events:[{kind:"launch_step", detail:{version:2,step:"goal",target:"A1"}}]
  }));
  await tick();
  const aside = f.node(".wr-plan-panel").innerHTML, html = f.node(".wr-timeline").innerHTML;
  assert.match(aside, /需要你关注[\s\S]*卡住了 · 切换网关[\s\S]*先做哪项/);
  assert.match(aside, /建好任务 · A1/);
  assert.doesNotMatch(html,/内部讨论/);
  assert.match(html, /创意顾问[\s\S]*Codex · 实际模型 · 强度 high · 用量待确认[\s\S]*能否简化[\s\S]*可以/);
  assert.match(html, /常规顾问[\s\S]*0 tokens/);
  f.click("internal"); assert.match(f.node(".wr-pane-content").innerHTML, /内部讨论/);
  f.click("members"); assert.match(f.node(".wr-pane-content").innerHTML, /支付/);
});

test("各出口发送对应操作，关闭后只读，追加预算提交新的总上限", async () => {
  for (const [button, initial, payload, reason, label] of [
    ["cancel", room(), {room:3}, "cancelled", "筹备已撤销"],
    ["stop", room({phase:"active"}), {room:3}, "user_stop", "你叫停了"],
    ["end_budget", room({phase:"active", budget:{spent:500000,tokens:500000,paused_at:"时间"}}), {room:3}, "budget", "预算用完了"],
    ["close", room({legacy:true,phase:"active"}), {room:3,summary:"已交接"}, "legacy", "已退出旧房"],
    ["budget", room({phase:"active", budget:{spent:520000,tokens:500000,paused_at:"时间"}}), {room:3,budget_tokens:900000}, null, ""]
  ]) {
    const f = harness(initial, {mutation(name, data, setRoom) {
      assert.equal(name,button);
      setRoom({...initial,budget:null,status:reason?"closed":"open",close_reason:reason});
      return {};
    }});
    await tick();
    const surfaces = f.node(".wr-main-head").innerHTML + f.node(".wr-notice").innerHTML;
    assert.match(surfaces, new RegExp(`data-wr="${button}"`));
    assert.equal(surfaces.includes("退出旧房"),initial.legacy);
    f.click(button);
    if (button === "budget") assert.match(f.node(".wr-confirm form").innerHTML, /min="520001"/);
    await f.confirm();
    assert.deepEqual(f.calls.find(call => call.name === button).data,payload);
    assert.equal(f.node(".cx-composer-wrap").hidden,Boolean(reason));
    assert.match(f.node(".wr-main-head").innerHTML,new RegExp(label));
    if (button === "budget") assert.equal(f.node(".wr-notice").hidden,true);
  }
});

test("历史可逐个进入，六种结束说法明确且列表本身没有逐个取详情", async () => {
  const reasons = ["completed","user_stop","abandoned","budget","cancelled","legacy"];
  const f = harness(null,{directory:[room({id:7,phase:"active",legacy:true}),room({id:8}), ...reasons.map((close_reason,i) => room({id:10+i,status:"closed",close_reason}))]});
  await tick();
  const html = f.node(".wr-directory").innerHTML;
  for (const label of ["做完了","你叫停了","领头放弃了","预算用完了","筹备已撤销","已退出旧房"]) assert(html.includes(label));
  assert.match(html, /data-wr-route="7"[\s\S]*data-wr-route="8"/);
  f.host.onclick({target:{closest:selector => selector === "[data-wr-route]" ? {dataset:{wrRoute:"8"}} : null}, preventDefault(){}});
  assert.deepEqual(f.routes,[8]);
  assert.deepEqual(f.calls,[]);
});

test("没房时能交给领头开局，重复提交占位时进入既有房", async () => {
  const f = harness(null,{directory:[],mutation:() => {throw Object.assign(Error("已经有房"),{details:{code:"room_occupied",detail:{room_id:9}}});}});
  assert.match(f.node(".wr-directory").innerHTML,/开作战室/);
  f.click("open");
  await f.confirm();
  assert.deepEqual(f.calls[0],{name:"open",data:{title:"签名",goal:"统一",lead:"pay"}});
  assert.deepEqual(f.routes,[9]);
});

test("作战室入口按列表字段判定，等你定盖过作战中，历史房忽略", () => {
  const {rail} = harness(null,{directory:[]});
  const open = extra => ({status:"open", phase:"preparing", draft_version:0, open_decisions:0, budget:null, ...extra});
  assert.equal(rail.roomState([{...open({draft_version:2}),status:"closed"}]),"empty");
  assert.equal(rail.roomState([open()]),"preparing");
  assert.equal(rail.roomState([open(),open({phase:"active"})]),"active");
  for (const waiting of [open({draft_version:2}),open({open_decisions:1}),open({budget:{paused_at:"时间"}})]) {
    const rooms = [open({phase:"active"}), waiting];
    assert.equal(rail.roomState(rooms),"waiting");
    assert.match(rail.roomMarkup("A",rooms),/等你定[\s\S]*rail-dot/);
  }
  assert.match(rail.roomMarkup("A",[]),/war-room=0[\s\S]*没房/);
});

test("领头放弃的理由按房内消息展示，旧房退出只出现在仍开放的旧房", async () => {
  const f = harness(room({status:"closed",close_reason:"abandoned",messages:[message(1,"assistant","放弃理由：依赖撤下")]}));
  await tick();
  assert.match(f.node(".wr-timeline").innerHTML,/放弃理由：依赖撤下/);
  assert.doesNotMatch(f.node(".wr-main-head").innerHTML,/退出旧房|撤销筹备|叫停/);
});
