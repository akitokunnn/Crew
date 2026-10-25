"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {createDocument, Event, visibleText} = require("./mini_dom.cjs");
const source = fs.readFileSync(path.join(__dirname, "../crew/web/storage-ui.js"), "utf8");
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve)); };
const copy = value => JSON.parse(JSON.stringify(value));
const fixture = () => ({home:"C:\\隔离数据", scanned_at:"2026-10-26T01:00:00Z", total_bytes:10000, reclaimable_bytes:2000, recyclable_bytes:3000, trash_bytes:1000,
  categories:[{id:"logs", label:"日志", description:"过期日志可以释放空间", bytes:2500, file_count:3, candidate_count:2, reclaimable_bytes:2000, recyclable_bytes:0, default_selected:true},
    {id:"backups", label:"备份", description:"保留最近三份备份", bytes:4000, file_count:4, candidate_count:1, reclaimable_bytes:0, recyclable_bytes:3000, default_selected:false},
    {id:"history", label:"工作历史", description:"工作历史保留", bytes:2500, file_count:1, candidate_count:0, reclaimable_bytes:0, recyclable_bytes:0, default_selected:false}],
  policy:{automatic_enabled:true, retention_days:30, keep_backups:3, recycle_days:7}, workspaces:[{id:"ws", name:"演示", bytes:2500, file_count:1}], warnings:[], last_maintenance:null,
  history:[{batch_id:"old", created_at:"2026-10-25T01:00:00Z", status:"completed", freed_bytes:20, recycled_bytes:1000, file_count:1, restorable_count:1, trash_bytes:1000, expires_at:"2026-11-01T01:00:00Z", errors:[]}]});
const preview = () => ({preview_id:"p1", expires_at:"2099-11-01T01:00:00Z", freed_bytes:2000, recycled_bytes:3000, file_count:3, categories:["logs", "backups"],
  protected:["运行中的后台、项目文件与聊天历史会保留"], samples:[{label:"备份", path:"C:\\隔离数据\\backups\\old.db", bytes:3000, action:"trash"}]});
function harness(overrides = {}) {
  const document = createDocument(), create = document.createElement;
  document.createElement = tag => { const element = create(tag); element.scrollIntoView = () => {}; return element; };
  const sandbox = {window:{}, document}; vm.createContext(sandbox); vm.runInContext(source, sandbox);
  let snapshot = fixture(); const calls = [];
  const options = Object.fromEntries(["read", "preview", "clean", "restore", "emptyTrash", "savePolicy"].map(action => [action, async body => {
    calls.push({action, body:body === undefined ? undefined : copy(body)});
    if (overrides[action]) return overrides[action](body, snapshot);
    if (action === "read") return copy(snapshot);
    if (action === "preview") return preview();
    if (action === "savePolicy") { snapshot.policy = copy(body); return copy(body); }
    return {status:"completed", freed_bytes:2000, recycled_bytes:3000, file_count:3, errors:[]};
  }]));
  const host = document.createElement("main"); document.body.append(host);
  const dispose = sandbox.window.CrewStorage.mount(host, options), action = name => host.querySelector(`[data-storage-action="${name}"]`);
  const field = (name, value) => { const input = host.querySelector(`[data-storage-policy="${name}"]`); input.value = value; input.dispatchEvent(new Event("input", {bubbles:true})); };
  return {document, host, dispose, calls, action, field, text:() => visibleText(host), setSnapshot:value => {snapshot = value;}};
}

test("storage uses backend safe defaults, makes two byte outcomes explicit, and cancelling preserves the selection and focus", async () => {
  const h = harness(); await flush();
  const logs = h.host.querySelector('[data-storage-category="logs"]'), backup = h.host.querySelector('[data-storage-category="backups"]');
  assert.equal(logs.checked, true); assert.equal(backup.checked, false); assert.equal(h.host.querySelector('[data-storage-category="history"]').disabled, true);
  backup.focus(); backup.click(); assert.equal(h.document.activeElement, backup, "Changing selection keeps the focused DOM control");
  h.action("preview").click(); await flush();
  assert.deepEqual(h.calls.at(-1), {action:"preview", body:{categories:["logs", "backups"]}});
  assert.match(h.text(), /立即释放.*移入回收站 · 保留 7 天/);
  assert.match(h.text(), /回收站仍占用磁盘/); assert.match(h.text(), /运行中的后台、项目文件与聊天历史会保留/);
  assert.equal(h.document.activeElement.id, "storage-preview-title");
  assert.equal(h.host.querySelector('[data-storage-category="logs"]').disabled, true, "Preview and selection cannot disagree");
  h.action("cancel-preview").click();
  assert.equal(h.host.querySelector('[data-storage-category="backups"]').checked, true);
  assert.equal(h.document.activeElement, h.action("preview")); assert.equal(h.calls.filter(item => item.action === "clean").length, 0);
});

test("a pending cleanup submits once, partial failure reports only actual results and refreshes history", async () => {
  let finish;
  const h = harness({clean:() => new Promise(resolve => {finish = resolve;})}); await flush();
  h.action("preview").click(); await flush();
  h.action("confirm-clean").click(); h.action("confirm-clean").click(); await flush();
  assert.equal(h.calls.filter(item => item.action === "clean").length, 1);
  assert.deepEqual(h.calls.at(-1), {action:"clean", body:{preview_id:"p1"}});
  const next = fixture(); next.history.unshift({...next.history[0], batch_id:"new", status:"partial"}); h.setSnapshot(next);
  finish({batch_id:"new", status:"partial", freed_bytes:1000, recycled_bytes:0, file_count:1, errors:[{path:"C:\\old.log", error:"文件仍被占用"}]}); await flush();
  assert.match(h.text(), /清理部分完成/); assert.match(h.text(), /实际释放 1000 B，移入回收站 0 B，处理 1 个文件/); assert.match(h.text(), /C:\\old.log：文件仍被占用/);
  assert.equal(h.action("confirm-clean"), null); assert.equal(h.host.querySelectorAll('[data-storage-action="restore"]').length, 2);
});

test("restore conflicts keep their real reason; emptying trash requires a separate confirmation and is single-flight", async () => {
  let emptied = 0, finish;
  const h = harness({restore:() => ({status:"partial", errors:[{path:"C:\\existing.png", error:"恢复位置已有文件，未覆盖"}]}), emptyTrash:() => {emptied++; return new Promise(resolve => {finish = resolve;});}}); await flush();
  h.action("restore").click(); await flush(); assert.match(h.text(), /恢复未全部完成.*恢复位置已有文件，未覆盖/);
  h.action("empty-trash").click(); assert.equal(emptied, 0); assert.match(h.text(), /永久删除.*清空后无法恢复/);
  h.action("cancel-empty").click(); assert.equal(h.action("confirm-empty"), null); assert.equal(h.document.activeElement, h.action("empty-trash"));
  h.action("empty-trash").click(); h.action("confirm-empty").click(); h.action("confirm-empty").click(); assert.equal(emptied, 1);
  finish({status:"completed", errors:[]}); await flush(); assert.match(h.text(), /回收站已清空/);
});

test("read errors can be retried; refresh and failed saves retain a policy draft", async () => {
  let reads = 0, failSave = true;
  const h = harness({read:(_body, snapshot) => { if (++reads === 1) throw Error("磁盘访问被拒绝"); return copy(snapshot); }, savePolicy:body => { if (failSave) throw Error("策略文件只读"); return copy(body); }});
  await flush(); assert.match(h.text(), /读取存储失败：磁盘访问被拒绝/);
  h.action("refresh").click(); await flush(); h.field("retention_days", "90");
  assert.equal(h.action("save-policy").disabled, false); assert.match(h.text(), /有尚未保存的修改/);
  h.action("save-policy").click(); await flush(); assert.match(h.text(), /策略文件只读/); assert.equal(h.host.querySelector('[data-storage-policy="retention_days"]').value, "90");
  h.action("refresh").click(); await flush(); assert.equal(h.host.querySelector('[data-storage-policy="retention_days"]').value, "90");
  failSave = false; h.action("save-policy").click(); await flush(); assert.match(h.text(), /维护策略已保存/); assert.equal(h.action("save-policy").disabled, true);
});

test("a completed operation whose readback fails is never automatically repeated or hidden", async () => {
  let reads = 0;
  const h = harness({read:(_body, snapshot) => { if (++reads > 1) throw Error("统计暂时不可用"); return copy(snapshot); }}); await flush();
  h.action("preview").click(); await flush(); h.action("confirm-clean").click(); await flush();
  assert.match(h.text(), /清理完成/); assert.match(h.text(), /操作已有结果，但刷新存储失败：统计暂时不可用/);
  assert.equal(h.calls.filter(item => item.action === "clean").length, 1);
  assert.equal(h.action("confirm-clean"), null);
});

test("disposing drops delayed reads and mutations without touching a newly mounted page", async () => {
  let finish;
  const h = harness({read:() => new Promise(resolve => {finish = resolve;})});
  h.dispose(); h.host.replaceChildren(h.document.createTextNode("新的设置页面")); finish(fixture()); await flush(); assert.equal(h.text(), "新的设置页面");
  let cleanFinish;
  const second = harness({clean:() => new Promise(resolve => {cleanFinish = resolve;})}); await flush(); second.action("preview").click(); await flush(); second.action("confirm-clean").click();
  second.dispose(); second.host.replaceChildren(second.document.createTextNode("已关闭")); cleanFinish({status:"completed", errors:[]}); await flush();
  assert.equal(second.text(), "已关闭"); assert.equal(second.calls.filter(item => item.action === "read").length, 1, "Unmounted mutations do not start a readback");
});

test("empty storage and expired previews have clear, non-destructive states", async () => {
  const empty = fixture(); empty.total_bytes = 0; empty.trash_bytes = 0; empty.categories.forEach(item => {item.bytes = 0; item.candidate_count = 0;}); empty.history = []; empty.workspaces = [];
  const h = harness({read:() => empty}); await flush(); assert.match(h.text(), /当前没有符合保留策略的可清理项/); assert.equal(h.action("preview").disabled, true); assert.equal(h.action("empty-trash").disabled, true);
  const expired = harness({preview:() => ({...preview(), expires_at:"2000-01-01T00:00:00Z"})}); await flush(); expired.action("preview").click(); await flush();
  assert.match(expired.text(), /预览已过期/); assert.equal(expired.action("confirm-clean").disabled, true); assert.equal(expired.calls.filter(item => item.action === "clean").length, 0);
});

test("ten storage categories keep actionable items before the button, fold occupied retained data, and omit zero rows", async () => {
  const snapshot = fixture();
  for (const id of ["exports", "backend_cache", "attachments", "runtime", "other", "trash", "management"]) snapshot.categories.push({id, label:id, description:"保留说明", bytes:id === "other" || id === "exports" ? 0 : 100,
    file_count:1, candidate_count:0, reclaimable_bytes:0, recyclable_bytes:0, default_selected:false});
  assert.equal(snapshot.categories.length, 10);
  const h = harness({read:() => snapshot}); await flush();
  assert.deepEqual(h.host.querySelectorAll("[data-storage-actionable] input").map(item => item.dataset.storageCategory), ["logs", "backups"]);
  const retained = h.host.querySelector('[data-storage-details="retained"]');
  assert(retained); assert.equal(retained.open, false); assert.match(visibleText(retained.querySelector("summary")), /保留中的数据 · /);
  assert.equal(retained.querySelectorAll("input").length, 6);
  assert.equal(h.host.querySelector('[data-storage-category="other"]'), null); assert.equal(h.host.querySelector('[data-storage-category="exports"]'), null);
  assert.equal(h.host.querySelectorAll(".st-legend [role=listitem]").length, 8);
  const topCard = h.host.querySelector(".st-content").children[0], sequence = topCard.children;
  assert(sequence.indexOf(h.action("preview").parentNode) < sequence.indexOf(retained), "The action stays ahead of protected details");
  h.action("preview").click(); await flush(); assert.deepEqual(h.calls.at(-1).body, {categories:["logs"]});
});

test("persisted maintenance failures and concrete protected reasons remain available after reopening", async () => {
  const snapshot = fixture();
  snapshot.last_maintenance = {at:"2026-10-26T01:00:00Z",status:"failed",error:"回收清单无法读取",errors:[{path:"旧日志",error:"文件正在被使用"}]};
  snapshot.categories.find(item => item.id === "backups").protected_reasons = ["最近 3 份备份之一"];
  const h = harness({read:() => snapshot}); await flush();
  assert.match(h.text(), /自动维护未完成：回收清单无法读取/);
  assert.match(h.text(), /旧日志：文件正在被使用/);
  assert.match(h.text(), /最近 3 份备份之一/);
  assert(h.host.querySelector('[data-storage-details="maintenance-errors"]'));
  assert(h.host.querySelector('[data-storage-details="reasons-backups"]'));
});
