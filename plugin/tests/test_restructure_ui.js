"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const SOURCE = fs.readFileSync(path.join(__dirname, "../../frontend/restructure-ui.js"), "utf8");

function load() {
  const handlers = {}, overlay = {hidden:true, innerHTML:"", contains:() => false, addEventListener:(type, fn) => { handlers[type] = fn; },
    querySelector:() => ({focus(){}, disabled:false, textContent:""}), querySelectorAll:() => []};
  const sandbox = {window:{}, CSS:{escape:value => value}, document:{createElement:() => overlay, body:{append(){}}, addEventListener(){}, activeElement:null}};
  vm.createContext(sandbox); vm.runInContext(SOURCE, sandbox);
  return {ui:sandbox.window.CrewRestructure, overlay, handlers};
}
const part = (id, name, parent_id = null) => ({id, name, parent_id});
const place = (session_id, name, domain_id, lead = false) => ({session_id, name, domain_id, lead});

test("the outline marks each part and leader against the other side and escapes names", () => {
  const {ui} = load();
  const before = {parts:[part("app", "应用"), part("front", "前端", "app"), part("back", "后端", "app")],
    leaders:[place("a", "甲", "front", true), place("b", "<乙>", "back")]};
  const after = {parts:[part("app", "应用"), part("front", "客户端", "app"), part("mobile", "手机端", "app")],
    leaders:[place("a", "甲", "front", true), place("b", "<乙>", "mobile")]};
  const right = ui.outline(after, before, "after", new Map([["mobile", ["丙"]]]));
  assert.match(right, /class="rs-part renamed" style="--level:1"><span class="rs-status">原名 前端<\/span><span class="rs-name">客户端/);
  assert.match(right, /class="rs-part new"[^>]*><span class="rs-status">新建<\/span><span class="rs-name">手机端/);
  assert.match(right, /rs-leader in">&lt;乙&gt; · 移入/);
  assert.match(right, /rs-leader lead">甲 · 负责/);
  assert.match(right, /rs-leader planned">丙 · 待启动/);
  const left = ui.outline(before, after, "before");
  assert.match(left, /class="rs-part removed"[^>]*><span class="rs-status">删除<\/span><span class="rs-name">后端/);
  assert.match(left, /rs-leader out">&lt;乙&gt; · 移出/);
  assert.match(ui.outline({parts:[], leaders:[]}, before, "after"), /没有分项/);
  const both = ui.compare(before, after, {beforeTitle:"现在", afterTitle:"回滚后"});
  assert.match(both, /data-showing="after"/);
  assert.match(both, /<h3>现在<\/h3>.*<h3>回滚后<\/h3>/s);
});

test("a structural difference reads as counts, skipping what did not change", () => {
  const {ui} = load();
  const empty = {added:[], removed:[], renamed:[], moved:[]};
  assert.equal(ui.diffSummary({parts:{...empty, added:[1, 2], removed:[1]}, leaders:{placed:[1], moved:[1], unplaced:[], lead_on:[1], lead_off:[]}}),
    "新建 2 个分项、删掉 1 个分项、调整 2 位 Leader、变更 1 处负责人");
});

test("the panel previews the plan as given and executes it with the edited drafts and explicit wake choices", async () => {
  const {ui, overlay, handlers} = load();
  const calls = [], toasts = [];
  const plan = {ok:true, nothing:false, board_version:7, revision:"r1", errors:[], before:{parts:[], leaders:[]}, after:{parts:[], leaders:[]},
    changes:[{index:0, label:"合并 leader：「甲」并入「乙」", error:null}], leaders:[], goals:[{goal_id:"g", title:"登录", action:"transfer", implicit:true, wakes:true, leader:{name:"甲"}, to:{name:"乙"}}],
    archived:[], to_start:[], summary:{merged:1}, handoffs:[{index:0, from:{session_id:"a", name:"甲"}, to:{session_id:"b", name:"乙"}, delivers:true, text:"草稿", chars:2, truncated:false}]};
  const panel = ui.create({toast:text => toasts.push(text), leaders:() => [{session_id:"a", name:"甲"}, {session_id:"b", name:"乙"}],
    onApplied:async result => calls.push(["applied", result.version]),
    api:async (url, body) => { calls.push([url, JSON.parse(JSON.stringify(body))]); return url === "/api/op/domains.restructure.preview" ? plan : {version:9, to_start:[], starts_turn:true, wakes:[{name:"乙"}]}; }});
  const changes = [{type:"leader.merge", leader:"a", into:"b"}];
  panel.open({project:"demo", changes, note:"合并甲"});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls[0], ["/api/op/domains.restructure.preview", {project:"demo", changes}]);
  assert.match(overlay.innerHTML, /会唤醒 乙 开工/);
  assert.match(overlay.innerHTML, /执行后会唤醒：乙（任务「登录」）/);
  handlers.input({target:{dataset:{rsDraft:"0:a:b"}, value:"改过的交接稿"}});
  handlers.click({target:{closest:() => ({dataset:{rsAct:"confirm"}, disabled:false})}});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls[1], ["/api/op/domains.restructure.apply", {project:"demo", changes, note:"合并甲", board_version:7, revision:"r1",
    handoffs:[{index:0, text:"改过的交接稿", wake:false}]}]);
  assert.deepEqual(calls[2], ["applied", 9]);
  assert.match(toasts[0], /存为版本 9.*已唤醒 乙/);
  assert.equal(overlay.hidden, true);
});

test("a stale preview is recomputed instead of executed", async () => {
  const {ui, overlay, handlers} = load();
  let previews = 0;
  const plan = {ok:true, nothing:false, board_version:1, revision:"r", errors:[], before:{parts:[], leaders:[]}, after:{parts:[], leaders:[]},
    changes:[{index:0, label:"x", error:null}], leaders:[], goals:[], archived:[], to_start:[], summary:{}, handoffs:[]};
  const panel = ui.create({toast(){}, leaders:() => [], onApplied:async () => {}, api:async url => {
    if (url === "/api/op/domains.restructure.preview") { previews++; return plan; }
    const error = Error("预览之后架构图又被改动过"); error.status = 409; throw error;
  }});
  panel.open({project:"demo", changes:[{type:"part.delete", part:"p"}], note:"删"});
  await new Promise(resolve => setImmediate(resolve));
  handlers.click({target:{closest:() => ({dataset:{rsAct:"confirm"}, disabled:false})}});
  await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve));
  assert.equal(previews, 2);
  assert.equal(overlay.hidden, false);
  assert.match(overlay.innerHTML, /已重新预览，请再核对一次/);
});
