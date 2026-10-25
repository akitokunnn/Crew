"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {createDocument, pressEscape, visibleText} = require("./mini_dom.cjs");
const source = fs.readFileSync(path.join(__dirname, "../crew/web/maintenance-ui.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(handler) {
  const document = createDocument(), timers = new Map(), calls = [], cleaned = [];
  let nextTimer = 0;
  const sandbox = {window:{}, document, URLSearchParams, setTimeout:(callback, delay) => { timers.set(++nextTimer, {callback, delay}); return nextTimer; },
    clearTimeout:identity => { timers.delete(identity); }};
  vm.createContext(sandbox); vm.runInContext(source, sandbox);
  const opener = document.createElement("button"); opener.textContent = "打开"; document.body.append(opener); opener.focus();
  const ui = sandbox.window.CrewMaintenance.create({api:async (route, body) => { calls.push({route, body:body === undefined ? undefined : JSON.parse(JSON.stringify(body))}); return handler(route, body, calls); },
    onCleaned:project => cleaned.push(project)});
  const dialog = ui.element;
  const action = name => dialog.querySelector(`[data-maintenance-action="${name}"]`);
  const tick = async () => { const pending = [...timers.values()]; timers.clear(); for (const timer of pending) timer.callback(); await flush(); await flush(); };
  return {document, ui, dialog, calls, cleaned, opener, timers, action, tick, text:() => visibleText(dialog)};
}

const departmentPreview = {preview_id:"p1", total:3, available:2, group:"boss", leader:{session_id:"boss", name:"boss"}, people:[
  {session_id:"boss", name:"boss", role:"leader", contexts:[
    {target:"boss", session_id:"boss", context:"总对话", runtime:"claude", goal_status:null, available:true, kind:null, reason:"离线，将临时恢复原会话执行压缩，完成后停止"},
    {target:"exec-1", session_id:"boss", context:"目标「已上线」", runtime:"codex", goal_status:"completed", available:true, kind:null, reason:"离线，将临时恢复原会话执行压缩，完成后停止"}]},
  {session_id:"worker", name:"worker", role:"member", contexts:[
    {target:"worker", session_id:"worker", context:"总对话", runtime:"claude", goal_status:null, available:false, kind:"busy", reason:"正在工作，没有打断；空闲后可重新预览再试"}]}]};
const item = (position, target, label, status, extra = {}) => ({position, target, label, status, error_type:null, error:null, detail:{}, ...extra});
const runningJob = {id:"job-1", kind:"department_compact", status:"running", created_at:"2026-10-26T01:00:00Z", detail:{}, error:null, error_type:null,
  counts:{pending:1, running:1, completed:0, failed:0, interrupted:0},
  items:[item(0, "boss", "boss · 总对话", "running"), item(1, "exec-1", "boss · 目标「已上线」", "pending")]};
const finishedJob = {...runningJob, status:"partial", counts:{pending:0, running:0, completed:1, failed:1, interrupted:0},
  items:[item(0, "boss", "boss · 总对话", "completed", {detail:{process:"temporary", pre_tokens:64000, post_tokens:5100}}),
         item(1, "exec-1", "boss · 目标「已上线」", "failed", {error_type:"identity_in_use", error:"此人员的总对话正在运行；为不同时占用同一身份，没有启动压缩进程"})]};

test("department preview lists real states, keeps unavailable items unselectable and submits the chosen contexts once", async () => {
  let resolveStart; let jobs = [];
  const h = harness((route, body) => {
    if (route.startsWith("/api/maintenance/jobs?")) return {jobs, cleanups:0};
    if (route === "/api/maintenance/department/preview") return departmentPreview;
    if (route === "/api/maintenance/department/compact") return new Promise(resolve => { resolveStart = resolve; });
    throw Error("unexpected " + route);
  });
  const opening = h.ui.openDepartment("demo", {session_id:"boss", name:"boss"});
  assert.equal(h.dialog.open, true);
  assert.equal(h.document.activeElement, h.dialog.querySelector("#maintenance-title"), "Focus moves into the dialog title");
  await opening;
  assert.deepEqual(h.calls.map(call => call.route), ["/api/maintenance/jobs?project=demo&kind=department_compact&leader_session_id=boss", "/api/maintenance/department/preview"]);
  assert.deepEqual(h.calls[1].body, {project:"demo", leader_session_id:"boss"});
  assert.equal(visibleText(h.dialog.querySelector("#maintenance-title")), "压缩整个部门「boss」");
  assert.match(h.text(), /对话与任务记忆.*保留现有团队/);
  assert.match(h.text(), /聊天记录、文件和交付都会保留/);
  assert(!h.text().includes('目标completed'));
  const boxes = h.dialog.querySelectorAll("input[data-maintenance-target]");
  assert.deepEqual(boxes.map(box => [box.dataset.maintenanceTarget, box.checked, box.disabled]), [["boss", true, false], ["exec-1", true, false], ["worker", false, true]]);
  assert.match(visibleText(h.dialog.querySelector("#maintenance-context-worker-state")), /^正在工作：正在工作，没有打断/);
  assert.equal(visibleText(h.action("start")), "压缩所选 2 项");
  const toggle = h.dialog.querySelector("#maintenance-context-exec-1");
  h.dialog.querySelector('.mt-body').scrollTop = 240;
  toggle.focus(); toggle.click();  // Space on the focused checkbox
  assert.equal(visibleText(h.action("start")), "压缩所选 1 项");
  assert.equal(h.document.activeElement.id, "maintenance-context-exec-1", "Re-rendering keeps keyboard focus on the toggled checkbox");
  assert.equal(h.dialog.querySelector('.mt-body').scrollTop, 240, 'Toggling a selection keeps the reading position');
  h.dialog.querySelector("#maintenance-context-worker").click();
  assert.equal(h.dialog.querySelector("#maintenance-context-worker").checked, false, "A busy context cannot be selected");
  h.dialog.querySelector("#maintenance-context-exec-1").click();
  h.action("start").click();
  assert.equal(h.action("start").disabled, true);
  h.action("start").click();
  await flush();
  assert.equal(h.calls.filter(call => call.route === "/api/maintenance/department/compact").length, 1, "Pending submission cannot be repeated");
  assert.deepEqual(h.calls.at(-1).body, {project:"demo", leader_session_id:"boss", preview_id:"p1", targets:["boss", "exec-1"]});
  jobs = [runningJob];
  resolveStart(runningJob); await flush(); await flush();
  assert.match(h.text(), /后台进行中 · 完成 0 \/ 失败 0 \/ 处理中 1 \/ 未开始 1/);
  assert.match(h.text(), /可以关闭窗口，之后重新打开查看结果/);
  assert.equal(h.action("start"), null, "Running jobs expose no second start");
  assert.equal(h.timers.size, 1);

  jobs = [finishedJob];
  await h.tick();
  const rows = h.dialog.querySelectorAll("li.mt-item");
  assert.deepEqual(rows.map(row => row.dataset.itemStatus), ["completed", "failed"]);
  assert.match(visibleText(rows[0]), /已压缩.*压缩前 6\.4 万 → 压缩后 5100；压缩完成，会话已停止/);
  assert.match(visibleText(rows[1]), /失败正在处理其他工作：此人员的总对话正在运行/);
  assert.match(h.text(), /部分完成 · 完成 1 \/ 失败 1/);
  assert.equal(h.timers.size, 0, "Polling stops once the job finishes");
  assert.ok(h.action("refresh"), "A finished job can be re-previewed to retry failures");
});

test("closing keeps the background job and reopening shows its stored progress; Escape restores focus", async () => {
  const h = harness(route => {
    if (route.startsWith("/api/maintenance/jobs?")) return {jobs:[runningJob], cleanups:0};
    throw Error("A running job must not be previewed again: " + route);
  });
  await h.ui.openDepartment("demo", {session_id:"boss", name:"boss"});
  assert.match(h.text(), /后台进行中/);
  pressEscape(h.dialog);
  assert.equal(h.dialog.open, false);
  assert.equal(h.document.activeElement, h.opener, "Focus returns to the menu entry that opened the dialog");
  await h.tick();
  assert.equal(h.calls.length, 1, "A closed dialog stops polling");
  h.opener.focus();
  await h.ui.openDepartment("demo", {session_id:"boss", name:"boss"});
  assert.equal(h.dialog.open, true);
  assert.deepEqual(h.dialog.querySelectorAll("li.mt-item").map(row => row.dataset.itemStatus), ["running", "pending"]);
  h.action("close").click();
  assert.equal(h.dialog.open, false);
});

test("failed preview and failed submission show the real error and allow an explicit retry", async () => {
  let failPreview = true, failStart = true;
  const h = harness(route => {
    if (route.startsWith("/api/maintenance/jobs?")) return {jobs:[], cleanups:0};
    if (route === "/api/maintenance/department/preview") { if (failPreview) throw Error("leader 不属于当前工作区，或部门已经结束"); return departmentPreview; }
    if (route === "/api/maintenance/department/compact") { if (failStart) throw Error("预览已超过 10 分钟，请重新预览"); return runningJob; }
    throw Error("unexpected " + route);
  });
  await h.ui.openDepartment("demo", {session_id:"boss", name:"boss"});
  const alert = h.dialog.querySelector(".mt-error");
  assert.equal(alert.getAttribute("role"), "alert");
  assert.equal(visibleText(alert), "leader 不属于当前工作区，或部门已经结束");
  failPreview = false;
  h.action("retry").click(); await flush(); await flush();
  h.action("start").click(); await flush(); await flush();
  const note = h.dialog.querySelector(".mt-note");
  assert.equal(note.getAttribute("role"), "alert");
  assert.equal(visibleText(note), "预览已超过 10 分钟，请重新预览");
  assert.equal(h.action("start").disabled, false, "The user can retry after a real failure");
  failStart = false;
  h.action("start").click(); await flush(); await flush();
  assert.match(h.text(), /后台进行中/);
});

const cleanupPreview = {preview_id:"c1", empty:false, blocked:null, cleanups:1, project:{name:"demo", root:"C:/work/demo", display_name:"演示"},
  departments:[{name:"boss", leader_session_id:"boss", members:2}], identities:{total:4, leaders:1, members:2, pending:1, git_admin:true},
  people:[{session_id:"boss", name:"boss", role:"leader", running:true, busy:true, external:false, managed:true},
          {session_id:"m1", name:"vscode-member", role:"member", running:false, busy:false, external:true, managed:true}],
  goals:{unfinished:[{id:"g1", title:"未完成功能", status:"running"}], finished:3}, channel:{messages:12, digests:1}, handoffs:4,
  context:5, todos:2, decisions:1, claims:1, leases:1, running:{processes:1, busy:1, external:1, unknown:null},
  kept:{documents:6, reports:3, finished_goals:3, crew_rules:2}};
const oldCleanup = {id:"old", kind:"workspace_cleanup", status:"completed", created_at:"2026-10-20T08:00:00Z", error:null, error_type:null,
  detail:{boundary:{channel:{after:0, upto:9}, digest:{after:0, upto:1}, handoff:{after:0, upto:3}}}, counts:{completed:1}, items:[item(0, "archive", "归档", "completed")]};

test("cleanup preview states the workspace scope, everything kept and the external risk before an explicit confirmation", async () => {
  let jobs = [oldCleanup];
  const doneJob = {id:"new", kind:"workspace_cleanup", status:"completed", created_at:"2026-10-26T01:00:00Z", error:null, error_type:null,
    detail:{boundary:{channel:{after:9, upto:21}, digest:{after:1, upto:2}, handoff:{after:3, upto:7}}}, counts:{completed:2, failed:0},
    items:[item(0, "session:boss", "停止「boss」的托管执行", "completed"), item(1, "archive", "归档部门与身份，旧交流、交接与上下文退出后续装载", "completed")]};
  const h = harness((route, body) => {
    if (route.startsWith("/api/maintenance/jobs?")) return {jobs, cleanups:jobs.filter(job => job.status === "completed").length};
    if (route === "/api/maintenance/cleanup/preview") return cleanupPreview;
    if (route === "/api/maintenance/cleanup") { jobs = [doneJob, oldCleanup]; return {...doneJob, status:"running", counts:{running:1}}; }
    if (route.startsWith("/api/maintenance/archive?")) {
      const params = new URLSearchParams(route.split("?")[1]);
      if (params.get("kind") === "channel" && !params.has("before")) return {kind:"channel", has_more:true, digest:{body:"## 已定结论\n旧要点", created_at:"2026-10-25T01:00:00Z"},
        messages:[{id:20, speaker_name:"boss", created_at:"2026-10-25T02:00:00Z", body:"较新的旧发言", mentions:[]}]};
      if (params.get("kind") === "channel") return {kind:"channel", has_more:false, digest:null,
        messages:[{id:12, speaker_name:"用户", created_at:"2026-10-25T01:30:00Z", body:"更早的旧发言", mentions:[{name:"boss"}]}]};
      return {kind:"handoffs", has_more:false, handoffs:[{id:5, from_name:"worker", from_session:"m1", to_target:"*", body:"旧广播", created_at:"2026-10-25T01:00:00Z"}]};
    }
    throw Error("unexpected " + route);
  });
  await h.ui.openCleanup("demo", "演示");
  assert.equal(visibleText(h.dialog.querySelector("#maintenance-title")), "清理当前工作区「演示」");
  assert.match(h.text(), /范围：仅当前工作区/);
  for (const expected of [/部门「boss」：leader 与 2 名成员/, /待收编 1/, /Git 管理员（关闭 Git 管理，不执行 Git 命令、不改动分支）/, /未完成目标 1 个：未完成功能/,
    /开放待办 2 项、待拍板 1 项、文件声明 1 项、设备租约 1 项/, /交流区发言 12 条、要点卡 1 张/, /交接 4 条/, /任务记忆 5 条/,
    /项目文件与目录：C:\/work\/demo/, /交付文档 6 份、目标报告 3 份/, /账号、连接与模型设置/, /全局规则 2 条/, /已清理 1 次/,
    /vscode-member：外部运行，需在原工具中停止/])
    assert.match(h.text(), expected);
  assert.equal(h.dialog.querySelector(".mt-warning").getAttribute("role"), "alert");
  const confirm = h.action("start");
  assert.equal(visibleText(confirm), "归档旧工作并重新开始");
  assert.ok(confirm.classList.contains("mt-danger"));
  confirm.click(); confirm.click(); await flush(); await flush();
  assert.deepEqual(h.calls.filter(call => call.route === "/api/maintenance/cleanup").map(call => call.body), [{project:"demo", preview_id:"c1", confirm:true}]);
  await h.tick();
  assert.match(h.text(), /已归档旧工作。新的 leader 不会收到旧交流、旧交接或旧上下文/);
  assert.deepEqual(h.cleaned, ["demo"], "The workspace reloads exactly once after success");
  await h.tick();
  assert.deepEqual(h.cleaned, ["demo"]);

  h.dialog.querySelector('[data-focus-key="archive-channel-new"]').click(); await flush(); await flush();
  assert.equal(visibleText(h.dialog.querySelector("#maintenance-title")), "清理前的交流（只读）");
  assert.match(h.text(), /旧要点/);
  h.action("archive-more").click(); await flush(); await flush();
  assert.equal(new URLSearchParams(h.calls.at(-1).route.split("?")[1]).get("before"), "20");
  assert.deepEqual(h.dialog.querySelectorAll(".mt-archive li pre").map(visibleText), ["更早的旧发言", "较新的旧发言"]);
  assert.match(h.text(), /用户 → @boss/);
  h.action("back").click();
  assert.match(h.text(), /清理完成/);
  h.dialog.querySelector('[data-focus-key="archive-handoffs-new"]').click(); await flush(); await flush();
  assert.match(h.text(), /worker → 所有人.*旧广播/);
});

test("an empty or blocked workspace cannot be confirmed", async () => {
  for (const preview of [{...cleanupPreview, empty:true}, {...cleanupPreview, blocked:"部门压缩正在进行，完成后再清理"}]) {
    const h = harness(route => route.startsWith("/api/maintenance/jobs?") ? {jobs:[], cleanups:0} : preview);
    await h.ui.openCleanup("demo", "演示");
    assert.equal(h.action("start").disabled, true);
    assert.match(visibleText(h.dialog.querySelector(".mt-warning")), preview.empty ? /没有可清理的旧工作/ : /部门压缩正在进行/);
    h.action("start").click(); await flush();
    assert.equal(h.calls.filter(call => call.route === "/api/maintenance/cleanup").length, 0);
  }
});

test("a failed cleanup keeps its real partial result visible without reloading the workspace", async () => {
  const failed = {id:"bad", kind:"workspace_cleanup", status:"partial", created_at:"2026-10-26T01:00:00Z", error:null, error_type:null, detail:{},
    counts:{completed:1, failed:2}, items:[item(0, "session:boss", "停止「boss」的托管执行", "completed"),
      item(1, "session:m1", "停止「vscode-member」的托管执行", "failed", {error_type:"external", error:"Claude 会话仍在外部运行，Crew 没有持有该进程，请到原工具停止后重试"}),
      item(2, "archive", "归档", "failed", {error_type:"blocked", error:"有身份未确认停止或目标未确认取消；没有归档，也没有建立清理边界。处理后请重新预览"})]};
  const h = harness(route => route.startsWith("/api/maintenance/jobs?") ? {jobs:[failed], cleanups:0} : cleanupPreview);
  await h.ui.openCleanup("demo", "演示");
  h.dialog.querySelector('[data-focus-key="show-job-bad"]').click();
  assert.match(h.text(), /部分完成/);
  assert.match(h.text(), /外部运行：Claude 会话仍在外部运行/);
  assert.match(h.text(), /没有执行：有身份未确认停止/);
  assert.match(h.text(), /已经停止或取消的部分保持现状，没有建立清理边界/);
  assert.equal(h.dialog.querySelector('[data-focus-key="archive-channel-bad"]'), null, "No history view is offered without a boundary");
  assert.deepEqual(h.cleaned, []);
});
