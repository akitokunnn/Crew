"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {create} = require("../crew/web/usage-ui.js");
const {create:createActions} = require("../crew/web/ui-actions.js");
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function report(label = "Claude组员") {
  return {tree:[{leader_session_id:"leader", name:"协调", tokens:14400, cost:0.051, share:100, codex_runs:3, codex_tokens:8500,
    self:{tokens:3600, cost:0.01275, codex_runs:1, codex_tokens:500}, members:[
      {session_id:"worker", name:label, tokens:10800, cost:0.03825, share:75, codex_runs:0, codex_tokens:0},
      {session_id:"codex-worker", name:"Codex组员 <保留>", tokens:0, cost:0, share:0, codex_runs:2, codex_tokens:8000}]}],
    daily:[{date:"2026-10-27", display_name:label, total_tokens:10800, cost_usd:0.03825}],
    models:[{model:"模型 <名称>", source:"Claude & Codex", input_tokens:3000, output_tokens:600, total_tokens:3600}]};
}
function fixture(overrides = {}) {
  const main = {innerHTML:""}, calls = [], renders = [], actions = createActions();
  const module = create({api(path) { calls.push(path); return Promise.resolve(report()); }, queryPath:path => `${path}&project=fixture`,
    ui:{icon:() => "", avatar:() => "", date:() => "10/28 18:00", time:() => "17:00"}, actions,
    replace(node, html) { node.innerHTML = html; renders.push({node, html}); }, ...overrides});
  module.update({project:{name:"fixture", display_name:"工作区 <A>"}, limits:{claude:{seven_day:null, five_hour:null}, codex:null}});
  const dispatch = (action, dataset = {}) => actions.dispatch(action, {element:{dataset}});
  return {module, main, calls, renders, actions, dispatch, async mount() { module.mount(main); await module.load(); }};
}

test("usage renders member Claude and Codex totals through its public lifecycle and owns all tabs", async () => {
  const f = fixture(); await f.mount(); const html = f.main.innerHTML;
  assert(html.includes("Claude组员")); assert(html.includes("Codex组员 &lt;保留&gt;")); assert(!html.includes("Codex组员 <保留>"));
  assert(html.includes("工作区 &lt;A&gt;"));
  for (const value of ["Codex 8.5K · 3 次", "Codex 500 · 1 次", "Codex 8K · 2 次", "75%", "Claude Token", "Claude 折合", "Claude 占比", "Claude 14.4K token · Codex 8.5K token"]) assert(html.includes(value), value);
  f.dispatch("usage-fold", {id:"leader"});
  assert(!f.main.innerHTML.includes("Claude组员")); assert(!f.main.innerHTML.includes("leader 本身"));
  assert(f.main.innerHTML.includes("Codex 8.5K · 3 次"));
  f.dispatch("usage-tab", {tab:"daily"});
  assert(f.main.innerHTML.includes("Claude组员")); assert(f.main.innerHTML.includes("2026-10-27"));
  assert(f.main.innerHTML.includes("/api/usage/export?period=week&amp;what=daily&amp;project=fixture"));
  f.dispatch("usage-tab", {tab:"models"});
  assert(f.main.innerHTML.includes("模型 &lt;名称&gt;")); assert(f.main.innerHTML.includes("Claude &amp; Codex"));
  assert(f.main.innerHTML.includes("/api/usage/export?period=week&amp;what=sessions&amp;project=fixture"));
  assert.equal(f.calls.length, 1);
});

test("account summaries preserve unknown/reset observations and Crew estimates without inventing zero usage", async () => {
  const f = fixture();
  const limit = {used_percent:87.3, resets_at:"2026-10-28", captured_at:"2026-10-27", expired:false,
    crew:{points:0.2, cost_usd:23, usd_per_point:10, usd_per_point_range:[8, 12], low:0.1, high:0.3}};
  f.module.update({project:{name:"fixture"}, limits:{claude:{seven_day:null, five_hour:{...limit, used_percent:null, expired:true}}, codex:limit}});
  await f.mount();
  const summary = f.module.summaryMarkup();
  assert(summary.includes('data-act="view" data-view="usage"')); assert(summary.includes("Claude 每周 未采集"));
  assert(summary.includes("周 未采集 · 5h 已重置")); assert(summary.includes("周 87.3%")); assert(!summary.includes("周 0%"));
  assert(f.main.innerHTML.includes("其中 Crew ≈ &lt;1%（折合 $23）"));
  assert(f.main.innerHTML.includes("采集已过期")); assert(f.main.innerHTML.includes("10/28 18:00 重置 · 17:00 采集"));
  assert(f.main.innerHTML.includes('width:87.3%;background:var(--warn)'));
  f.module.update({project:{name:"fixture"}, limits:{claude:{seven_day:{...limit, used_percent:null}, five_hour:null}, codex:null}});
  assert(f.module.summaryMarkup().includes("周 未采集 · 5h 未采集"));
});

test("changing periods rejects late successes and failures and fetches each newly selected period once", async () => {
  const requests = [], f = fixture({api(path) { const request = deferred(); requests.push({path, ...request}); return request.promise; }});
  f.module.mount(f.main); f.dispatch("usage-period", {period:"today"});
  assert.deepEqual(requests.map(item => item.path), ["/api/usage?period=week", "/api/usage?period=today"]);
  const renders = f.renders.length; requests[0].resolve(report("上周迟到")); await settle();
  assert.equal(f.renders.length, renders); assert(!f.main.innerHTML.includes("上周迟到"));
  requests[1].resolve(report("今天完成")); await settle(); assert(f.main.innerHTML.includes("今天完成"));
  f.dispatch("usage-period", {period:"all"}); f.dispatch("usage-period", {period:"week"});
  const afterSwitch = f.renders.length; requests[2].reject(Error("旧周期失败")); await settle();
  assert.equal(f.renders.length, afterSwitch); assert.equal(f.module.snapshot().error, null);
  requests[3].resolve(report("本周完成")); await settle();
  assert(f.main.innerHTML.includes("本周完成")); assert(!f.main.innerHTML.includes("今天完成"));
  f.dispatch("usage-period", {period:"week"}); assert.equal(requests.length, 4);
});

test("leaving the page preserves filters/collapse and caches a late report without replacing another view", async () => {
  const request = deferred(), f = fixture({api(path) { f.calls.push(path); return request.promise; }});
  f.module.mount(f.main); f.module.setTab("models"); f.module.toggle("leader");
  f.module.unmount(); f.main.innerHTML = "另一个页面"; const renders = f.renders.length;
  request.resolve(report("离页后完成")); await settle();
  assert.equal(f.main.innerHTML, "另一个页面"); assert.equal(f.renders.length, renders);
  f.module.update({project:{name:"fixture"}, limits:null}); assert.equal(f.renders.length, renders);
  f.module.mount(f.main); await f.module.load();
  assert.equal(f.calls.length, 1); assert.equal(f.module.snapshot().tab, "models");
  assert.deepEqual(f.module.snapshot().collapsed, ["leader"]);
  f.module.setTab("tree"); assert(f.main.innerHTML.includes('aria-expanded="false"'));
  f.module.toggle("leader"); assert(f.main.innerHTML.includes("离页后完成"));
  f.module.unmount(); f.dispatch("usage-period", {period:"all"});
  assert.equal(f.module.snapshot().period, "week"); assert.equal(f.calls.length, 1);
});

test("failed reads retain the real reason and retry explicitly without mount or snapshot-update loops", async () => {
  const requests = [], f = fixture({api() { const request = deferred(); requests.push(request); return request.promise; }});
  f.module.mount(f.main); requests[0].reject(Error("读取失败 <保留原因>")); await settle();
  assert.equal(f.module.snapshot().loading, false); assert.equal(f.module.snapshot().error, "读取失败 <保留原因>");
  assert(f.main.innerHTML.includes("读取失败 &lt;保留原因&gt;")); assert(f.main.innerHTML.includes('data-act="usage-retry"'));
  assert(!f.main.innerHTML.includes("正在读取用量…"));
  f.module.unmount(); f.module.mount(f.main); f.module.update({project:{name:"fixture"}, limits:null});
  assert.equal(requests.length, 1);
  f.dispatch("usage-retry"); f.dispatch("usage-retry"); assert.equal(requests.length, 2);
  assert.equal(f.module.snapshot().error, null); assert.equal(f.module.snapshot().loading, true);
  requests[1].resolve(report("重试成功")); await settle();
  assert.equal(f.module.snapshot().error, null); assert(f.main.innerHTML.includes("重试成功"));
  f.dispatch("usage-refresh"); requests[2].reject(Error("刷新失败")); await settle();
  assert(f.main.innerHTML.includes("重试成功")); assert(f.main.innerHTML.includes("刷新失败"));
});

test("project changes invalidate report, folds and pending requests before rendering the next workspace", async () => {
  const requests = [], f = fixture({api() { const request = deferred(); requests.push(request); return request.promise; }});
  f.module.mount(f.main); f.module.toggle("leader"); f.module.setTab("daily");
  f.module.update({project:{name:"second", display_name:"另一个工作区"}, limits:null});
  assert.equal(requests.length, 2); assert.deepEqual(f.module.snapshot().collapsed, []);
  assert.equal(f.module.snapshot().tab, "daily"); assert(!f.main.innerHTML.includes("Claude组员"));
  const renders = f.renders.length; requests[0].resolve(report("工作区A")); await settle();
  assert.equal(f.renders.length, renders); assert.equal(f.module.snapshot().hasData, false);
  requests[1].resolve(report("工作区B")); await settle();
  assert(f.main.innerHTML.includes("工作区B")); assert(!f.main.innerHTML.includes("工作区A"));
  f.module.update({project:{name:"fixture"}, limits:null}); assert.equal(requests.length, 3);
  assert(!f.main.innerHTML.includes("工作区B")); requests[2].resolve(report("工作区A重新读取")); await settle();
});

test("destroy unregisters actions and drops late results without touching the departed host", async () => {
  const request = deferred(), f = fixture({api:() => request.promise});
  f.module.mount(f.main); assert.deepEqual(f.actions.owners(), ["usage"]);
  f.module.destroy(); const renders = f.renders.length;
  request.resolve(report("销毁后完成")); await settle();
  assert.equal(f.renders.length, renders); assert.equal(f.module.snapshot().hasData, false);
  assert.deepEqual(f.actions.owners(), []); assert.equal(f.dispatch("usage-refresh"), false);
  f.module.mount(f.main); f.module.update({project:{name:"fixture"}, limits:null}); f.module.setPeriod("all");
  await f.module.load({force:true}); assert.equal(f.renders.length, renders);
});
