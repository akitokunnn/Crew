"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const tick = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
const plain = value => JSON.parse(JSON.stringify(value));
const http = require("./fixtures/war_room_http.json");
const {plans:[plan3, plan2, plan1]} = http;
const shiftLines = (plan, from, amount) => plan.entries.map(item => item.line >= from ? {...item, line:item.line + amount} : item);
const message = (id, speaker_session_id, body, extra = {}) => ({id, speaker_session_id, body, speaker_name:speaker_session_id,
  created_at:"2026-11-28T02:03:00Z", mentions:[], notified:[], reply:null, ...extra});
const job = (id, kind, status, title) => ({entry_id:id, kind, status, title, owner_session_id:"pay", owner_name:"支付", note:""});
function room(extra = {}) {
  return {id:3, status:"open", phase:"preparing", draft_version:2, approved_version:null, legacy:false, close_reason:null, title:"签名",
    goal:"统一 v2", lead_session_id:"assistant", lead_name:"工作区助手", budget:null, messages:[message(1,"assistant","先写计划")],
    decisions:[], consults:[], plans:[plan2, plan1], board:[], events:[], has_more:false, origin:null, read_id:1, latest_id:1, unread:0, open_decisions:0, members:[], summary:null,
    watchdog:null, isolation:"worktree", isolation_reason:null, worktrees:[], isolation_members:[], unowned_changes:[],
    executions:[], unread_execution_events:0, lead_working_room_id:null, usage_pending:0, ...extra};
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
function harness(initial, {mobile = false, reads, mutation, directory = null, httpError} = {}) {
  const calls = [], toasts = [], copied = [], routes = [], nodes = new Map(), listeners = new Set();
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
      copyText:async text => { copied.push(text); }, pageProject:() => ({mode:"workspace", project:"A"}), enableChatLists(){}, chatSendKey:event => event.key === "Enter" && !event.isComposing,
      postOptions:data => ({body:JSON.stringify(data)})
    }};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const file of ["vendor/markdown-it.min.js","report-markdown.js","app-data.js","global-rail.js","channel.js","war-room-plan.js","war-room.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8"), sandbox, {filename:file});
  }
  const W = sandbox.CrewWarRoom;
  const form = node(".wr-confirm form"), submit = element();
  form.elements = {title:{value:"签名"}, goal:{value:"统一"}, lead:{value:"pay"}, summary:{value:"已交接"}, budget:{value:"900000"}};
  form.querySelector = selector => selector === ".wr-dialog-error" ? node("confirmation error") : submit;
  node(".wr-pane-dialog").querySelector = () => node("panel close");
  node(".wr-send").form = node("send form");
  let detail = initial;
  const client = sandbox.CrewAppData.create({origin:"http://localhost", project:"A",
    fetch:async () => ({ok:false, status:httpError.status, json:async () => httpError.body})});
  const api = async (route, data) => {
    const name = route.split(".").slice(1).join(".").split("?")[0];
    const query = new URL(route, "http://localhost").searchParams;
    calls.push({name, data:plain(data ?? null), ...(query.has("execution_id") ? {query:Object.fromEntries(query)} : {})});
    if (name === "get") return plain(reads ? await reads(detail, query) : detail);
    if (name === "mark_read" && !data.execution_id) return {read_id:data.id};
    if (httpError) return client.request(route, data);
    if (mutation) return mutation(name, data, value => { detail = value; });
    return {};
  };
  W.mount({host, id:directory === null ? 3 : 0, rooms:directory, roomsError:"", state, api, toast:text => toasts.push(text),
    openRoom:id => routes.push(id), refreshRooms(){}, onChanged(){}, openChannel(){}, avatar:() => ""});
  const click = (wr, attrs = {}) => host.onclick({target:{closest:selector => selector === "button[data-wr]" ? {dataset:{wr,...attrs}} : null}});
  const send = async () => { node("send form").onsubmit({preventDefault(){}}); await tick(); };
  const confirm = async () => { form.onsubmit({preventDefault(){}}); await tick(); };
  return {W, P:sandbox.CrewWarRoomPlan, rail:sandbox.CrewGlobalRail, calls, copied, node, host, toasts, routes, listeners, click, send, confirm,
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
  assert.match(aside.innerHTML, /领头建议 HMAC-SHA256[\s\S]*data-entry="Q1"/);
  assert.match(aside.innerHTML, /支付 · 切换网关/);
  assert.doesNotMatch(aside.innerHTML, /\[A1\]|\[Q1\]|<strong>Q1<\/strong>/);
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
  assert.equal(f.node("#wr-pane-title").innerHTML, "作战计划书 v2");
  assert.doesNotMatch(f.node(".wr-pane-content").innerHTML, /作战计划书/);
  assert.doesNotMatch(f.node(".wr-pane-content").innerHTML, /敲定，开打/);
  f.W.unmount();
  assert.equal(f.listeners.size, 0);
});

test("没有待定项时直接批准，连续点击只发一次写请求", async () => {
  let release;
  const latest = {...plan2, budget_tokens:null};
  latest.body = latest.body.replace(/^- \[Q1\].*\n/m, "");
  latest.entries = shiftLines(plan2, 10, -1).filter(item => item.entry_id !== "Q1");
  const f = harness(room({plans:[latest,plan1]}), {mutation:() => new Promise(resolve => { release = resolve; })});
  await tick();
  assert.match(f.node(".wr-plan-panel").innerHTML, /等你定<\/h2>\s*<p class="wr-no-decisions">暂无需要你拍板的事<\/p>/);
  f.click("approve",{version:"2"}); f.click("approve",{version:"2"});
  assert.equal(f.node(".wr-confirm").open,false);
  assert.deepEqual(f.calls.filter(call => call.name === "plan.approve").map(call => call.data),
    [{room:3, version:2, recommendations:{}, budget_tokens:null}]);
  f.reload(room({phase:"active", approved_version:2}));
  release({replayed:true});
  await tick();
  assert.equal(f.host.dataset.phase, "active");
  assert.deepEqual(f.toasts, []);
});

test("版本冲突保留所见版本，不会偷偷批准刷新后的计划；另外三种错误显示具体原因和行号", async () => {
  for (const [code, expected] of [
    ["version_conflict", /计划变了/],
    ["missing_recommendation", /没有推荐值/],
    ["invalid_criteria", /完成标准没写对（第 13 行）/],
    ["invalid_plan", /计划书有一处没写对（第 6 行）/]
  ]) {
    const f = harness(room(), {httpError:http.errors[code]});
    await tick();
    f.click("approve",{version:"2"});
    f.reload(room({plans:[plan3, plan2, plan1], draft_version:3}));
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
  assert.equal(f.node("#wr-pane-title").innerHTML, "作战计划书 v3");
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
  const moved = {...plan3, body:plan3.body.replace("- [A1] 支付 | 逐步切换网关 | 文件: gateway/\n- [A2] 支付 | 观察错误率 | 文件: metrics/",
    "- [A2] 支付 | 观察错误率 | 文件: metrics/\n- [A1] 支付 | 逐步切换网关 | 文件: gateway/"),
    entries:plan3.entries.map(item => item.entry_id === "A1" ? {...item, line:7} : item.entry_id === "A2" ? {...item, line:6} : item)};
  const f = harness(room({phase:"active", approved_version:2, draft_version:3, plans:[plan2, moved, plan1]}));
  await tick();
  const pane = f.node(".wr-pane-content");
  const {blocks:[other, target]} = watchPlan(f, pane, ["entry:A2", "entry:A1"]);
  f.click("jump", {version:"2", block:"entry:A1"});
  assert.equal(f.node(".wr-pane-dialog").open, true);
  assert.equal(f.node("#wr-pane-title").innerHTML, "作战计划书 v3");
  assert.match(pane.innerHTML, /观察错误率[\s\S]*逐步切换网关/);
  assert(target.scrolled && target.classList.contains("wr-flash"));
  assert.equal(other.scrolled, undefined);
});

test("旧正文锚点能跟随段落移动，同序号的另一段不会冒充已删除的段落", async () => {
  const original = {...plan2, body:plan2.body.replace("用统一的 v2。", "核对发布窗口。")};
  const moved = {...plan3, body:plan3.body.replace("用统一的 v2。", "先看回滚条件。\n\n核对发布窗口。"),
    entries:shiftLines(plan3, 4, 2)};
  const removed = {...moved, version:4, body:moved.body.replace("\n\n核对发布窗口。", ""), entries:plan3.entries, created_at:"2026-11-28T02:05:00Z"};
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
  assert.match(f.node(".wr-plan-panel").innerHTML, /作战计划书 <span>v4/);
  assert.deepEqual(f.toasts, ["这一段在 v4 已删除"]);
  assert.equal(other.scrolled, undefined);
});

test("历史锚点已删除时打开最新版并提示首次消失的版本，不滚动计划书", async () => {
  const deleted = {...plan3, body:plan3.body.replace("- [A1] 支付 | 逐步切换网关 | 文件: gateway/\n", ""),
    entries:shiftLines(plan3, 7, -1).filter(item => item.entry_id !== "A1")};
  const v4 = {...deleted, version:4, created_at:"2026-11-28T02:05:00Z"};
  for (const plans of [[deleted, plan2, plan1], [v4, deleted, plan2, plan1]]) {
    const version = plans[0].version;
    const f = harness(room({draft_version:version, plans}));
    await tick();
    const pane = f.node(".wr-plan-panel");
    const {blocks:[remaining], scroll} = watchPlan(f, pane, ["entry:A2"]);
    f.click("jump", {version:"2", block:"entry:A1"});
    assert.match(f.node(".wr-plan-panel").innerHTML, new RegExp(`作战计划书 <span>v${version}`));
    assert.deepEqual(f.toasts, ["这一段在 v3 已删除"]);
    assert.equal(scroll.scrollTop, 120);
    assert.equal(remaining.scrolled, undefined);
    assert(!remaining.classList.contains("wr-flash"));
  }
});

test("删条目优先跳到最新版原小节，小节后来删除或当版已删时提示具体版本", async () => {
  const heading = '["等你定","heading_open"]:1';
  const v3 = {...plan3, body:plan3.body.replace("- [Q2] 何时上线 | 周五 | 下周一\n", ""),
    entries:shiftLines(plan3, 12, -1).filter(item => item.entry_id !== "Q2")};
  const v4 = {...v3, version:4, body:v3.body.replace(/## 等你定\n[^\n]+\n\n/, ""),
    entries:shiftLines(v3, 11, -3).filter(item => item.entry_id !== "Q1"), created_at:"2026-11-28T02:05:00Z"};
  const f = harness(room({draft_version:3, plans:[v3, plan2, plan1]}), {mobile:true});
  await tick();
  const pane = f.node(".wr-pane-content");
  const {blocks:[section]} = watchPlan(f, pane, [heading]);
  assert.match(f.node(".wr-timeline").innerHTML, /data-block="[^"]*等你定[^"]*"><span>删<\/span> 何时上线/);
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
  const introduced = {...plan2, body:`临时说明\n\n${plan2.body}`, entries:shiftLines(plan2, 1, 2)};
  const f = harness(room({draft_version:3, plans:[plan3, introduced, plan1]}));
  await tick();
  const pane = f.node(".wr-plan-panel");
  const {scroll} = watchPlan(f, pane, ["entry:A1"]);
  assert.match(f.node(".wr-timeline").innerHTML, /data-version="3" data-block=""><span>删<\/span> 临时说明/);
  f.click("jump", {version:"3", block:""});
  assert.match(f.node(".wr-plan-panel").innerHTML, /作战计划书 <span>v3/);
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
  assert.equal(f.node("#wr-pane-title").innerHTML, "作战计划书 v2");
  assert.match(pane, /整理方案/);
  assert.doesNotMatch(pane, /逐步切换网关/);
  assert.deepEqual(f.toasts, []);
});

test("v1 锚点只显示版本、摘要和打开按钮，点开仍能查看初稿", async () => {
  const f = harness(room());
  await tick();
  const [card] = f.node(".wr-timeline").innerHTML.match(/<section class="wr-note wr-revision"><div><strong>计划书 v1<\/strong>[\s\S]*?<\/section>/);
  assert(card.includes(plan1.summary));
  assert.match(card, /data-version="1">看这一版/);
  assert.doesNotMatch(card, /<ul>|data-block=/);
  f.click("jump", {version:"1"});
  assert.match(f.node(".wr-plan-panel").innerHTML, /作战计划书 <span>v1/);
});

test("六个改动全部显示，更多改动可展开和收起", async () => {
  function revisedWithChanges(n) {
    const notes = Array.from({length:n}, (_, index) => `补充说明 ${index + 1}`).join("\n\n");
    return {...plan1, version:2, created_at:plan2.created_at, body:`${plan1.body.trimEnd()}\n\n${notes}\n`};
  }
  const six = harness(room({plans:[revisedWithChanges(6), plan1]}));
  await tick();
  const collapsed = six.node(".wr-timeline").innerHTML;
  assert.equal([...collapsed.matchAll(/data-block=/g)].length, 6);
  assert.doesNotMatch(collapsed, /data-wr="revision-more"/);

  const eight = harness(room({plans:[revisedWithChanges(8), plan1]}));
  const feed = eight.node(".wr-timeline");
  feed.querySelector = () => eight.node("revision expansion button");
  await tick();
  assert.equal([...feed.innerHTML.matchAll(/data-block=/g)].length, 6);
  assert.match(feed.innerHTML, /aria-expanded="false">\+2 项/);
  assert.doesNotMatch(feed.innerHTML, /补充说明 7|补充说明 8/);
  eight.click("revision-more", {version:"2"});
  assert.equal([...feed.innerHTML.matchAll(/data-block=/g)].length, 8);
  assert.match(feed.innerHTML, /补充说明 7[\s\S]*补充说明 8[\s\S]*aria-expanded="true">收起/);
  eight.click("revision-more", {version:"2"});
  assert.match(feed.innerHTML, /aria-expanded="false">\+2 项/);
  assert.doesNotMatch(feed.innerHTML, /补充说明 7|补充说明 8/);
});

test("手机摘要打开同一份计划并在单内敲定，正常发言和回复都发给领头会话", async () => {
  const f = harness(room(), {mobile:true, mutation:(_, data) => ({message:message(2,"user",data.body)})});
  await tick();
  assert.match(f.node(".wr-panelbar").innerHTML, /计划书 v2 · 1 项等你定/);
  f.click("panel");
  assert.equal(f.node(".wr-pane-dialog").open,true);
  assert.equal(f.node("#wr-pane-title").innerHTML, "作战计划书 v2");
  assert.doesNotMatch(f.node(".wr-pane-content").innerHTML, /作战计划书/);
  assert.match(f.node(".wr-pane-content").innerHTML, /敲定，开打/);
  f.click("hide-panel");
  f.click("reply",{message:"1"});
  f.node("textarea").value = "确认";
  await f.send();
  assert.deepEqual(f.calls.find(call => call.name === "post").data,{room:3,body:"确认",to:["assistant"],reply_to:1});
});

test("作战中战况显示卡住和待定项，成员和内部消息在独立抽屉，顾问用量未知时有明确提示", async () => {
  const asked = {id:8, status:"open", question:"先做哪项？", room_title:"签名", recommendation:"网关", answer:null, created_at:"2026-11-28T02:05:00Z"};
  const f = harness(room({phase:"active", approved_version:2, open_decisions:1, decisions:[asked],
    board:[job("A1","assignment","blocked","切换网关")], members:[{session_id:"pay",crew_name:"支付"}],
    messages:[message(1,"assistant","先写计划"),message(2,"pay","内部讨论")],
    usage_pending:2, consults:[{question_id:"g", purpose:"creative", runtime:"codex", model:"实际模型", effort:"high", tokens:null, usage_status:"pending",
      question:"能否简化？", response:"可以", created_at:"2026-11-28T02:04:00Z"}, {
      question_id:"h", purpose:"routine", runtime:"claude", model:"实际模型二", effort:"low", tokens:0,
      usage_status:"confirmed", question:"有没有遗漏？", response:"无", created_at:"2026-11-28T02:04:15Z"},
      {question_id:"i", purpose:"difficult", runtime:"claude", model:null, effort:"载体默认", native_model:"claude-haiku-5-5", tokens:null, usage_status:"pending",
      question:"可以上线吗？", response:null, error:"OAuth 登录过期", created_at:"2026-11-28T02:04:16Z"}],
    events:[{kind:"launch_step", detail:{version:2,step:"goal",target:"A1"}}]
  }));
  await tick();
  const aside = f.node(".wr-plan-panel").innerHTML, html = f.node(".wr-timeline").innerHTML;
  assert.match(aside, /需要你关注[\s\S]*卡住了 · 切换网关[\s\S]*先做哪项/);
  assert.match(aside, /建好任务 · A1/);
  assert.doesNotMatch(html,/内部讨论/);
  assert.match(html, /创意顾问<\/strong><span>用量待确认<\/span><small[^>]*>Codex · 实际模型 · 强度 high<\/small>[\s\S]*能否简化[\s\S]*可以/);
  assert.match(html, /常规顾问[\s\S]*0 tokens/);
  assert.match(html, /疑难顾问[\s\S]*Claude Code · 默认模型 · 强度 载体默认 · 实际 claude-haiku-5-5[\s\S]*顾问调用失败[\s\S]*OAuth 登录过期/);
  assert.match(aside, /另有 2 次用量待确认/);
  assert.match(html, /等你定[\s\S]*去拍板/);
  f.click("internal"); assert.match(f.node(".wr-pane-content").innerHTML, /内部讨论/);
  f.click("members"); assert.match(f.node(".wr-pane-content").innerHTML, /支付/);
});

test("每个被点名的人各有回执，取消原因可展开，内部帖同样显示", async () => {
  const waiting = {busy:"对方还在忙", stopped:"已停止", external:"在外部窗口", maintenance:"维护中",
    liveness_unknown:"还没确认状态", "failed|时间|原因":"上次没送成"};
  const mentions = Object.keys(waiting).map((reason, i) => ({session_id:"s"+i, name:"成员"+i, state:"queued", waiting:reason}));
  mentions.push({session_id:"pay", name:"支付", state:"cancelled", reason:"已取消：房间已结束"},
    {session_id:"ops", name:"运维", state:"delivered"}, {session_id:"qa", name:"测试", state:"replied"});
  const f = harness(room({messages:[message(1,"user","请确认",{mentions}), message(2,"pay","完成核对",{mentions})]}));
  await tick();
  f.click("internal");
  for (const html of [f.node(".wr-timeline").innerHTML, f.node(".wr-pane-content").innerHTML]) {
    for (const [i,label] of Object.values(waiting).entries()) assert(html.includes(`成员${i} · 待送达 · ${label}`));
    assert.match(html, /<details[^>]*data-delivery="cancelled"[^>]*><summary title="已取消：房间已结束">支付 · 已取消<\/summary><span>已取消：房间已结束/);
    assert.match(html, /运维 · 已送达/);
    assert.match(html, /测试 · 已回复/);
  }
});

test("用户可以回复领头的消息，自己的消息没有回复按钮", async () => {
  const f = harness(room({messages:[message(1,"assistant","先写计划"),message(2,"user","收到")]}));
  await tick();
  const html = f.node(".wr-timeline").innerHTML;
  assert.match(html, /data-wr="reply" data-message="1">回复/);
  assert.doesNotMatch(html, /data-wr="reply" data-message="2"/);
});

test("结束后摘要和战况只提示进度与结局，排队回执和未决拍板随房间结束", async () => {
  const queued = http.queued, [lead] = queued.mentions;
  const decision = {id:8, status:"open", question:"作战室《签名》：可以上线吗？", room_title:"签名", recommendation:"明天", answer:null, created_at:"2026-11-28T02:05:00Z"};
  for (const [phase, close_reason, label] of [["active","completed","做完了"],["preparing","cancelled","筹备已撤销"]]) {
    const f = harness(room({status:"closed", phase, close_reason, lead_session_id:lead.session_id,
      board:[job("A1","assignment","done","核对签名"),job("A2","assignment","blocked","发布")],
      messages:[{...queued, mentions:[{...lead, state:"queued", waiting:"busy"}]}], open_decisions:1, decisions:[decision]}), {mobile:true});
    await tick();
    const bar = f.node(".wr-panelbar").innerHTML, aside = f.node(".wr-plan-panel").innerHTML;
    assert.match(bar, new RegExp(`进度 1/2 · ${label}`));
    assert.doesNotMatch(bar, /等你定|卡住/);
    assert.match(aside, new RegExp(`分工进度 <span>1/2 · ${label}`));
    assert.doesNotMatch(aside, /等你定|需要你关注|可以上线吗/);
    f.click("panel");
    assert.equal(f.node("#wr-pane-title").innerHTML, "战况");
    assert.doesNotMatch(f.node(".wr-pane-content").innerHTML, /<h2>战况/);
    const feed = f.node(".wr-timeline").innerHTML;
    assert.match(feed, /data-delivery="undelivered">lead · 未送达<\/small>/);
    assert.match(feed, /wr-decision" data-state="closed"[^>]*><strong>随房间结束关闭<\/strong><small class="decision-source">作战室 · 签名<\/small><p>作战室《签名》：可以上线吗？<\/p>/);
    assert.doesNotMatch(feed, /待送达|领头还在忙|去拍板/);
  }
});

test("分工行按目标展示真实执行的未读数，历史翻页和已读只使用本执行的序号", async () => {
  const {id, goal_id, read_seq, latest_seq, unread, session_id, round, generation, status} = http.history;
  const summary = {id, goal_id, read_seq, latest_seq, unread, session_id, round, generation, status};
  const initial = room({phase:"active", approved_version:2, board:[http.job], executions:[summary], unread_execution_events:unread,
    lead_working_room_id:3, members:[{session_id, crew_name:"支付", working_room_id:3}]});
  const f = harness(initial, {mobile:true, reads(detail, query) {
    if (!query.has("execution_id")) return detail;
    return {...detail, executions:[query.has("before_seq") ? http.older : http.history]};
  }, mutation(name, data, setRoom) {
    assert.equal(name, "mark_read");
    setRoom({...initial, executions:[{...summary, read_seq:data.seq, unread:0}], unread_execution_events:0});
    return {room_id:data.room, execution_id:data.execution_id, read_seq:data.seq};
  }});
  await tick();
  assert.match(f.node(".wr-plan-panel").innerHTML, /切换网关[\s\S]*3 条未读/);
  assert.doesNotMatch(f.node(".wr-plan-panel").innerHTML, /条执行记录未读/);
  f.click("history", {execution:id});
  await tick();
  const pane = f.node(".wr-pane-content");
  assert.match(pane.innerHTML, /接口检查通过[\s\S]*回归已完成/);
  assert.deepEqual(f.calls.filter(call => call.name === "mark_read").map(call => call.data), [{room:3, execution_id:id, seq:latest_seq}]);
  assert.doesNotMatch(f.node(".wr-plan-panel").innerHTML, /3 条未读/);
  f.click("history-more");
  await tick();
  assert.deepEqual(f.calls.filter(call => call.query).at(-1).query,
    {id:"3", execution_id:id, limit:"50", before_seq:String(http.history.events[0].seq)});
  assert.match(pane.innerHTML, /请先验证回滚步骤[\s\S]*接口检查通过/);
  f.click("hide-panel");
  f.click("members");
  assert.equal((pane.innerHTML.match(/作战室中/g) || []).length, 2);
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
  const f = harness(null,{directory:[room({id:7,phase:"active",legacy:true,progress:{done:1,total:2}}),room({id:8}), ...reasons.map((close_reason,i) => room({id:10+i,status:"closed",close_reason}))]});
  await tick();
  const html = f.node(".wr-directory").innerHTML;
  assert.match(html, /统一 v2 · 领头 工作区助手/);
  assert.match(html, /data-wr-route="7"[^]*?<small>分工 1\/2<\/small>/);
  assert.equal(html.match(/分工 \d+\/\d+/g).length, 1);
  for (const label of ["做完了","你叫停了","领头放弃了","预算用完了","筹备已撤销","已退出旧房"]) assert(html.includes(label));
  assert.match(html, /data-wr-route="7"[\s\S]*data-wr-route="8"/);
  f.host.onclick({target:{closest:selector => selector === "[data-wr-route]" ? {dataset:{wrRoute:"8"}} : null}, preventDefault(){}});
  assert.deepEqual(f.routes,[8]);
  assert.deepEqual(f.calls,[]);
});

test("没房时能交给领头开局，重复提交占位时进入既有房", async () => {
  const f = harness(null,{directory:[],httpError:http.errors.room_occupied});
  assert.match(f.node(".wr-directory").innerHTML,/开作战室/);
  f.click("open");
  await f.confirm();
  assert.deepEqual(f.calls[0],{name:"open",data:{title:"签名",goal:"统一",lead:"pay"}});
  assert.deepEqual(f.routes,[http.room]);
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

test("停滞同时出现在侧栏、目录、手机摘要和收起战况，点提醒保留草稿并聚焦", async () => {
  const stalledRoom = room({phase:"active", progress:{done:0,total:1}, watchdog:{state:"attention", reason:"no_progress"}});
  const list = harness(null,{directory:[stalledRoom]});
  assert.match(list.node(".wr-directory").innerHTML, /data-state="attention">等你关注/);
  assert.match(list.rail.roomMarkup("A",[room(),stalledRoom]), /等你关注[\s\S]*rail-dot/);
  assert.equal(list.rail.roomState([room(),{...stalledRoom,status:"closed"}]),"waiting");
  const f = harness(stalledRoom,{mobile:true});
  await tick();
  f.click("collapse");
  assert.match(f.node(".wr-toggle").innerHTML,/等你关注/);
  assert.match(f.node(".wr-panelbar").innerHTML,/等你关注/);
  f.click("panel");
  assert.match(f.node(".wr-pane-content").innerHTML,/data-wr="ask-lead"[^>]*>房里停下了，叫醒领头仍没动静 · 问问领头 ›/);
  const input = f.node("textarea");
  input.value = "已有草稿";
  f.click("ask-lead");
  assert.equal(f.node(".wr-pane-dialog").open,false);
  assert.equal(input.focused,true);
  assert.equal(input.value,"已有草稿");
});

test("关注区只显示需要用户处理的状态，等待原因可单独出现", async () => {
  for (const [state,reason,label] of [["waiting_user","approval","在等你审批"],["waiting_user","decision","在等你定"],
    ["stalled",null,null],["woken",null,null]]) {
    const f = harness(room({phase:"active",watchdog:{state,reason}}));
    await tick();
    const html = f.node(".wr-plan-panel").innerHTML;
    if (label) {
      assert(html.includes(`<li>${label}</li>`));
      assert(f.node(".wr-panelbar").innerHTML.includes(label));
    } else assert.doesNotMatch(html,/需要你关注/);
  }
});

test("目录状态按授权行展示，失败单独触发关注，关房后收起关注", async () => {
  const rows = [["pending",null,"准备目录中"],["active",null,"独立目录运行中"],["awaiting_merge",null,"等合并"],
    ["removed","merged","已清理"],["removed","backed_up","已备份"],["releasing",null,"收尾中"],["failed",null,"没建好"]];
  const worktrees = rows.map(([state,outcome],i) => ({member:"s"+i,state,outcome,error:i === 6 ? "目录被占用" : null}));
  const members = rows.map((_,i) => ({session_id:"s"+i,crew_name:"成员"+i}));
  const isolation_members = members.map(item => ({session_id:item.session_id,member:item.crew_name,mode:"worktree",paths:["src/"],out_of_scope:[]}));
  const detail = room({phase:"active",worktrees,members,isolation_members});
  const f = harness(detail);
  await tick();
  assert.match(f.node(".wr-plan-panel").innerHTML,/成员6 的独立目录没建好：目录被占用<\/li>/);
  f.click("members");
  for (const [i,[,,label]] of rows.entries()) {
    assert.match(f.node(".wr-pane-content").innerHTML,new RegExp(`成员${i}</strong>\\s*<small[^>]*>${label}`));
    assert(f.node(".wr-plan-panel").innerHTML.includes(label));
  }
  f.reload({...detail, worktrees:[{member:"s6",state:"failed"}]});
  await tick();
  assert.match(f.node(".wr-plan-panel").innerHTML,/成员6 的独立目录没建好：原因见详情/);
  assert.doesNotMatch(f.node(".wr-pane-content").innerHTML,/已备份|目录被占用/);
  f.reload({...detail,status:"closed",close_reason:"user_stop",watchdog:{state:"attention"}});
  await tick();
  assert.doesNotMatch(f.node(".wr-plan-panel").innerHTML,/需要你关注/);
});

test("普通文件夹筹备提示覆盖空计划、计划和开打确认，升级前的房显示对应原因", async () => {
  const shared = {isolation:"none",isolation_reason:"not_git"};
  const note = "这是普通文件夹，大家共用同一目录直接改文件。";
  const f = harness(room({...shared, plans:[],draft_version:0,messages:[]}),{mobile:true});
  await tick();
  assert(f.node(".wr-plan-panel").innerHTML.includes(note));
  for (const [selector,text] of [[".wr-plan-panel","等领头写第一版计划。"],[".wr-timeline","还没有发言"]])
    assert(f.node(selector).innerHTML.includes(`class="wr-column-empty">${text}`));
  f.reload(room({...shared})); await tick();
  f.click("panel");
  assert(f.node(".wr-pane-content").innerHTML.includes(note));
  f.click("approve",{version:"2"});
  assert(f.node(".wr-confirm form").innerHTML.includes(note));
  f.click("dismiss");
  const answered = {...plan2, entries:plan2.entries.map(item => ({...item,answer:"同意"}))};
  f.reload(room({...shared, plans:[answered]})); await tick();
  f.click("approve",{version:"2"});
  assert.equal(f.node(".wr-confirm").open,true);
  assert(!f.calls.some(call => call.name === "plan.approve"));
  for (const [isolation_reason,label] of [["not_git", note.slice(0,-1)],["pre_upgrade","升级前开的房，没有隔离"]]) {
    const active = harness(room({...shared,phase:"active",isolation_reason}));
    await tick(); active.click("members");
    assert(active.node(".wr-pane-content").innerHTML.includes(label));
    assert.doesNotMatch(active.node(".wr-pane-content").innerHTML, /<strong>没有隔离/);
  }
});

test("混合房按成员显示范围和目录，开放与关房时均可展开文件问题", async () => {
  const paths = ["src/pay/", "tools/<fixture>.py", "docs/验收说明.md"];
  const isolation_members = [
    {member:"支付",session_id:"pay",mode:"shared",paths,out_of_scope:["src/<other>.py","tools/check.py"]},
    {member:"验收",session_id:"qa",mode:"worktree",paths:["tests/"],out_of_scope:[]}
  ];
  const detail = room({phase:"active",isolation:"mixed",isolation_reason:"lead_choice",isolation_members,
    unowned_changes:["old/<unused>.md"],worktrees:[{member:"qa",state:"active"}]});
  const f = harness(detail, {mobile:true});
  await tick();
  const status = f.node(".wr-plan-panel"), drawer = f.node(".wr-pane-content");
  f.click("members");
  for (const surface of [status,drawer]) {
    assert.match(surface.innerHTML,/部分成员开了独立目录，其余共用目录。/);
    assert.match(surface.innerHTML,/支付<\/strong>\s*<small class="wr-folder-tag">共用目录/);
    assert.match(surface.innerHTML,/验收<\/strong>\s*<small[^>]*>独立目录运行中/);
    assert.match(surface.innerHTML,/<summary><span>负责文件<\/span><code[^>]*>src\/pay\/、tools\/&lt;fixture&gt;.py、docs\/验收说明.md<\/code><\/summary>\s*<ul><li><code>src\/pay\/<\/code>[\s\S]*?<li><code>docs\/验收说明.md<\/code>/);
    assert.match(surface.innerHTML,/<summary>改了范围外的文件 2 个<\/summary>\s*<ul><li><code>src\/&lt;other&gt;.py<\/code>[\s\S]*?<code>tools\/check.py<\/code>/);
    assert.doesNotMatch(surface.innerHTML,/<other>|<fixture>|这里没有隔离/);
  }
  for (const statusValue of ["open","closed"]) {
    f.reload({...detail,status:statusValue,close_reason:statusValue === "closed" ? "completed" : null});
    await tick();
    const attention = status.innerHTML.match(/<section class="wr-attention">[\s\S]*?<\/section>/)[0];
    assert.match(attention,/<summary>支付 改了分工范围外的文件 ›<\/summary>\s*<ul><li><code>src\/&lt;other&gt;.py/);
    assert.match(attention,/<summary>共用目录里有不属于任何分工的改动 1 个 ›<\/summary>\s*<ul><li><code>old\/&lt;unused&gt;.md/);
    assert.doesNotMatch(attention,/验收 改了/);
  }
  f.reload({...detail,worktrees:[],unowned_changes:[],isolation_members:isolation_members.map(item => ({...item,out_of_scope:[]}))});
  await tick();
  assert.doesNotMatch(status.innerHTML,/需要你关注|独立目录运行中/);
  assert.match(status.innerHTML,/验收<\/strong>\s*<small>独立目录/);
  f.reload({...detail,isolation:"none",worktrees:[],isolation_members:[isolation_members[0]]});
  await tick();
  assert.match(status.innerHTML,/领头选择全员共用目录，按文件范围分工。/);
  assert.doesNotMatch(status.innerHTML,/这里没有隔离|部分成员开了/);
});

test("计划书用原分工锚点渲染文件和可选标签，历史计划仍可读", async () => {
  const assignments = plan3.entries.filter(item => item.kind === "assignment");
  const entries = plan3.entries.map(item => item.kind === "assignment" ?
    {...item,paths:["src/<main>.js","docs/"],worktree:item.entry_id === assignments[0].entry_id} : item);
  const lines = plan3.body.split("\n");
  for (const item of entries.filter(entry => entry.kind === "assignment"))
    lines[item.line-1] += " | 文件: src/<main>.js, docs/" + (item.worktree ? " | 独立目录" : "");
  const f = harness(room({draft_version:3,plans:[{...plan3,entries,body:lines.join("\n")},plan2]}));
  await tick();
  const html = f.node(".wr-plan-panel").innerHTML;
  for (const item of assignments) {
    const row = html.match(new RegExp(`<li data-wr-block="entry:${item.entry_id}">[\\s\\S]*?</li>`))[0];
    assert(row.includes(item.owner + " · " + item.title));
    assert.match(row,/文件：<code>src\/&lt;main&gt;.js、docs\/<\/code>/);
    assert.equal(row.includes('class="wr-folder-tag">独立目录'),item === assignments[0]);
  }
  assert.doesNotMatch(f.P.document(plan2).html,/wr-folder-tag|wr-plan-files/);
});

test("Git 开打前显示两组成员，合计最多六个名字，无待定项也先确认", async () => {
  const people = Array.from({length:9}, (_,i) => ({member:`成员${i}`,session_id:`s${i}`,mode:i < 7 ? "worktree" : "shared",paths:[`src/${i}/`],out_of_scope:[]}));
  const answered = {...plan2, entries:plan2.entries.map(item => ({...item,answer:"已定"}))};
  for (const [members, expected] of [
    [people, ["独立目录</strong> 成员0、成员1、成员2、成员3、成员4 等 7 人","共用目录</strong> 成员7 等 2 人"]],
    [people.slice(0,6), ["独立目录</strong> 成员0、成员1、成员2、成员3、成员4、成员5</p>"]],
    [people.slice(7), ["共用目录</strong> 成员7、成员8</p>"]]
  ]) {
    const mode = members[0].mode === "shared" ? "none" : members.length > 6 ? "mixed" : "worktree";
    const f = harness(room({plans:[answered],isolation:mode,isolation_reason:mode === "worktree" ? null : "lead_choice",isolation_members:members}));
    await tick(); f.click("approve",{version:"2"});
    assert.equal(f.node(".wr-confirm").open,true);
    const html = f.node(".wr-confirm form").innerHTML;
    for (const label of expected) assert(html.includes(label));
    assert.equal(html.match(/成员\d/g).length,Math.min(members.length,6));
    assert.equal(f.calls.filter(call => call.name === "plan.approve").length,0);
    await f.confirm();
    assert.equal(f.calls.filter(call => call.name === "plan.approve").length,1);
  }
});

test("备份命令只识别末行，点击复制原文并反馈", async () => {
  const command = "git branch crew/room-3/pay refs/crew-backup/room-3/pay";
  const f = harness(room({messages:[message(1,"assistant",`已备份。\r\n${command}`),
    message(2,"assistant",`${command}\n还有下一行`),message(3,"assistant","git branch feat refs/heads/main")]}));
  await tick();
  const html = f.node(".wr-timeline").innerHTML;
  assert.equal(html.match(/复制恢复命令/g).length,1);
  assert(html.includes(`<code>${command}</code>`));
  assert(html.includes(`data-command="${command}"`));
  f.click("copy-command",{command}); await tick();
  assert.deepEqual(f.copied,[command]);
  assert.deepEqual(f.toasts,["已复制恢复命令"]);
});

test("标题下的目标在目录和详情一致，去除句号并略过空目标和同名目标", async () => {
  for (const [goal, subtitle] of [[null,"领头 工作区助手"],[" 。 ","领头 工作区助手"],["签名。。","领头 工作区助手"],
    [" 统一 v2.。 ","统一 v2 · 领头 工作区助手"]]) {
    const detail = room({goal});
    assert(harness(null,{directory:[detail]}).node(".wr-directory").innerHTML.includes(`<small>${subtitle}</small>`));
    const f = harness(detail); await tick();
    assert(f.node(".wr-main-head").innerHTML.includes(`<p>${subtitle}</p>`));
  }
});

test("关注区有执行记录的卡住项和可跳转拍板有箭头，纯文字无箭头", async () => {
  const detail = room({phase:"active",board:[{...http.job,status:"blocked"},job("X","criterion","blocked","等签字")],
    executions:[http.history], decisions:[{id:8,status:"open",question:"可以开打吗？",created_at:plan2.created_at}]});
  const f = harness(detail,{mobile:true}); await tick(); f.click("panel");
  const html = f.node(".wr-pane-content").innerHTML.split("</section>")[0];
  assert.match(html,/data-wr="history"[^>]+>卡住了 · 切换网关[^<]* ›</);
  assert.match(html,/data-wr="decision-jump"[^>]+>可以开打吗？ ›</);
  assert.match(html,/<li>卡住了 · 等签字<\/li>/);
  f.click("history",{execution:http.history.id}); await tick();
  assert.equal(f.node("#wr-pane-title").innerHTML,"执行记录");
  assert.match(f.node(".wr-pane-content").innerHTML,/回归已完成/);
});
