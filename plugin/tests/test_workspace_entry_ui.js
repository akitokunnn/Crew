"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = path.join(__dirname, "../../frontend");
const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const helpers = {esc, ic: name => `<svg data-icon="${name}"></svg>`, display: item => item.display_name || item.name};
const palette = {"--accent":"#111", "--accent-text":"#222"};
const themes = [{id:"celadon", name:"青瓷", light:palette, dark:palette}];
const zero = {working:0, decisions:0, todos:0, approvals:0, waiting:0, acceptance:0, chat_unread:0, channel_unread:0, errors:0};
const state = (...names) => ({projects:names.map(name => ({name, display_name:null, theme:"celadon"})), themes, notification:{}});
const flush = () => new Promise(resolve => setImmediate(resolve));

// Fresh module per test: the entry keeps its activity cache in module scope.
function load() {
  const calls = [];
  const main = {dataset:{}, innerHTML:"", contains:() => false, querySelectorAll:() => []};
  const timers = new Map(), listeners = new Map();
  const ctx = vm.createContext({URLSearchParams, calls, main, now:0,
    setTimeout(fn, ms) { timers.set(fn, ctx.now + ms); return fn; }, clearTimeout(fn) { timers.delete(fn); }});
  ctx.window = ctx;
  ctx.document = {hidden:false, activeElement:null, addEventListener:(event, fn) => listeners.set(event, fn),
    getElementById:id => id === "main" ? main : {classList:{contains:name => name === "workspace-entry"}}};
  ctx.fetch = (url, init) => new Promise((resolve, reject) => calls.push({url, init, resolve, reject}));
  vm.runInContext("Date.now = () => now;", ctx);
  vm.runInContext(fs.readFileSync(path.join(web, "themes.js"), "utf8"), ctx);
  vm.runInContext(fs.readFileSync(path.join(web, "workspace-entry.js"), "utf8"), ctx);
  const render = (snapshot, changed) => ctx.CrewWorkspaceEntry.render(snapshot, helpers, changed);
  const advance = now => {
    ctx.now = now;
    for (const [fn, due] of timers) if (due <= now) { timers.delete(fn); fn(); }
  };
  const answer = async (body, ok = true) => { calls.at(-1).resolve({ok, status:ok ? 200 : 500, json:async () => body}); await flush(); await flush(); };
  return {ctx, calls, main, render, answer, advance, listeners};
}

test("activity invalidations during a request and its throttle share one trailing fetch", async () => {
  const entry = load(), home = state("a");
  entry.render(home);
  entry.render(home, true);
  await entry.answer({projects:[]});
  entry.advance(6000);
  entry.render(home, true);
  entry.advance(9999);
  assert.equal(entry.calls.length, 1);
  entry.advance(10000);
  assert.equal(entry.calls.length, 2);
  await entry.answer({projects:[{project:"a", counts:{...zero, todos:1}, items:[], unknown:false}]});
  assert.match(entry.main.innerHTML, /1 件等你处理/);
  entry.advance(30000);
  assert.equal(entry.calls.length, 2);
});

test("a hidden page keeps a throttled invalidation until visible", async () => {
  const entry = load(), home = state("a");
  entry.render(home);
  await entry.answer({projects:[]});
  entry.render(home, true);
  entry.ctx.document.hidden = true;
  entry.advance(10000);
  assert.equal(entry.calls.length, 1);
  entry.ctx.document.hidden = false;
  entry.listeners.get("visibilitychange")();
  assert.equal(entry.calls.length, 2);
});

test("links keep the desktop tab-guard contract: project cards carry no hash, the overview reaches only the 系统管理员", async () => {
  const entry = load();
  entry.render(state("自动驾驶 & 感知", "tools"));
  await entry.answer({projects:[{project:"自动驾驶 & 感知", counts:{...zero, decisions:1}, items:[{kind:"decision", title:"选方案", name:"感知", detail:""}], unknown:false, updated_at:"x"},
    {project:"tools", counts:zero, items:[], unknown:false, updated_at:"x"}]});
  const html = entry.render(state("自动驾驶 & 感知", "tools"));
  const cards = [...html.matchAll(/class="entry-card"[^>]*href="([^"]*)"/g)].map(match => match[1]);
  assert.deepEqual(cards, [`/?project=${esc(encodeURIComponent("自动驾驶 & 感知"))}`, "/?project=tools"]);
  // The overview has no workspace yet: 系统管理员 is reached directly; ideas need a workspace first.
  assert.match(html, /href="\/assistant\.html" title="系统管理员/);
  assert.doesNotMatch(html, /notes\.html|全局助手|想法墙/);
  assert.match(html, /<button type="button" data-local-control class="[^"]*" data-act="settings"/);
  assert.match(html, /class="entry-task"[^>]*href="\/\?project=%E8%87%AA[^"#]*#all"/);
});

test("activity is fetched once per project set, read-only, throttled and skipped while hidden", async () => {
  const entry = load();
  entry.render(state("a", "b"));
  assert.equal(entry.calls.length, 1);
  assert.equal(entry.calls[0].url, "/api/op/workspace.activity?project=a&project=b");
  assert.equal(entry.calls[0].init.headers["X-Crew"], "1");
  assert.equal(entry.calls[0].init.method, undefined);
  entry.render(state("a", "b"));
  assert.equal(entry.calls.length, 1, "no second request while one is in flight");
  await entry.answer({projects:[]});
  entry.ctx.now = 9999; entry.render(state("a", "b"));
  assert.equal(entry.calls.length, 1, "same set within 10 s is not refetched");
  entry.render(state("a", "b", "c"));
  assert.equal(entry.calls.length, 2, "a changed set is fetched right away");
  await entry.answer({projects:[]});
  entry.ctx.document.hidden = true; entry.ctx.now = 60000; entry.render(state("a", "b", "c"));
  assert.equal(entry.calls.length, 2, "hidden page does not fetch");
  entry.ctx.document.hidden = false; entry.render(state("a", "b", "c"));
  assert.equal(entry.calls.length, 3);
});

test("render is a pure function of state and cache, and fresh activity is painted without waiting for the next poll", async () => {
  const entry = load();
  const first = entry.render(state("a"));
  assert.equal(entry.render(state("a")), first);
  assert.match(first, /entry-skeleton/);
  assert.match(first, /entry-inbox-loading/);
  await entry.answer({projects:[{project:"a", counts:{...zero, todos:2}, items:[], unknown:false, updated_at:"x"}]});
  assert.match(entry.main.innerHTML, /2 件等你处理/);
  assert.equal(entry.main.dataset.render, entry.main.innerHTML);
  assert.equal(entry.render(state("a")), entry.main.innerHTML);
});

test("zero counts are not shown and quiet workspaces say so", async () => {
  const entry = load();
  entry.render(state("a", "b"));
  await entry.answer({projects:[{project:"a", counts:{...zero, decisions:2}, items:[], unknown:false, updated_at:"x"},
    {project:"b", counts:zero, items:[], unknown:false, updated_at:"x"}]});
  const html = entry.render(state("a", "b"));
  const badges = [...html.matchAll(/class="entry-badge" data-kind="([a-z]+)"/g)].map(match => match[1]);
  assert.deepEqual(badges, ["decision"]);
  assert.match(html, /<b>2<\/b>等你拍板/);
  assert.doesNotMatch(html, /<b>0<\/b>/);
  assert.match(html, /安静 · 没有待处理/);
  assert.doesNotMatch(html, /在推进/);
});

test("unknown runtime status is labelled unconfirmed instead of zero working", async () => {
  const entry = load();
  entry.render(state("a"));
  await entry.answer({projects:[{project:"a", counts:zero, items:[], unknown:true, updated_at:"x"}]});
  const html = entry.render(state("a"));
  assert.match(html, /data-kind="unknown">运行状态未确认/);
  assert.match(html, /1 个运行状态未确认/);
  assert.doesNotMatch(html, /0 在推进|个在推进/);
});

test("a project error and a failed request are shown with their real reason", async () => {
  const entry = load();
  entry.render(state("a", "gone"));
  await entry.answer({projects:[{project:"a", counts:zero, items:[], unknown:false, updated_at:"x"}, {project:"gone", error:"项目不存在: gone"}]});
  let html = entry.render(state("a", "gone"));
  assert.match(html, /data-state="error"[\s\S]*读取失败：项目不存在: gone/);
  assert.match(html, /1 个读取失败/);
  entry.ctx.now = 20000; entry.render(state("a", "gone"));
  entry.calls.at(-1).reject(new Error("Failed to fetch")); await flush(); await flush();
  html = entry.render(state("a", "gone"));
  assert.match(html, /动态读取失败：Failed to fetch · 下面是上次读到的结果/);
  const fresh = load();
  fresh.render(state("a"));
  await fresh.answer({error:"需要重新登录"}, false);
  html = fresh.render(state("a"));
  assert.match(html, /动态读取失败：需要重新登录/);
  assert.doesNotMatch(html, /上次读到/);
  assert.match(html, /动态未读取/);
  assert.doesNotMatch(html, /entry-skeleton/);
});

test("names, titles and errors are escaped", async () => {
  const entry = load();
  const name = `<img src=x onerror="alert(1)">`;
  entry.render(state(name));
  await entry.answer({projects:[{project:name, counts:{...zero, working:1, todos:1}, unknown:false, updated_at:"x",
    items:[{kind:"todo", title:"<script>x</script>", name:"<b>", detail:""}, {kind:"working", title:"a & b", name:"<i>", detail:""}]}]});
  const html = entry.render(state(name));
  assert.doesNotMatch(html, /<img|<script|<b>[^0-9]|<i>/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(html, /a &amp; b/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

test("attention items go to the cross-workspace inbox; cards keep the other activity", async () => {
  const entry = load();
  entry.render(state("a"));
  await entry.answer({projects:[{project:"a", counts:{...zero, working:1, decisions:1, approvals:1}, unknown:false, updated_at:"x",
    items:[{kind:"approval", title:"允许发布", name:"", detail:""}, {kind:"decision", title:"选方案", name:"甲", detail:""}, {kind:"working", title:"重构", name:"乙", detail:""}]}]});
  const html = entry.render(state("a"));
  const inbox = html.slice(html.indexOf("entry-inbox"), html.indexOf("entry-workspaces"));
  const cards = html.slice(html.indexOf("entry-workspaces"));
  assert.match(inbox, /权限确认[\s\S]*允许发布[\s\S]*等你拍板[\s\S]*选方案/);
  assert.doesNotMatch(inbox, /重构/);
  assert.match(cards, /2 件等你/);
  assert.match(cards, /entry-line-title">重构/);
  assert.doesNotMatch(cards, /允许发布|选方案/);
});

test("no workspaces explains how to start and does not fetch", () => {
  const entry = load();
  const html = entry.render(state());
  assert.match(html, /还没有工作区/);
  assert.match(html, /<code>crew start<\/code>/);
  assert.match(html, /新建工作区/);
  assert.equal(entry.calls.length, 0);
});
