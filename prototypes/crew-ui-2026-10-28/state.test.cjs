"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { create } = require("./state.js");
const sandbox = {};
vm.runInNewContext(
  fs.readFileSync(require.resolve("./data.js"), "utf8"),
  sandbox,
);
const sample = JSON.parse(JSON.stringify(sandbox.CrewSample));

test("同一任务跨工作区入口共用身份，成员与其他任务的草稿独立且刷新后保留", () => {
  const m = create(sample);
  m.draft("voice", "leader", "给负责人");
  m.draft("voice", "engine", "给引擎成员");
  m.draft("crew", "leader", "另一任务");
  const restored = create(sample, JSON.parse(JSON.stringify(m.state)));
  assert.equal(restored.task("voice").related[0], "crew");
  assert.equal(restored.draft("voice", "leader"), "给负责人");
  assert.equal(restored.draft("voice", "engine"), "给引擎成员");
  assert.equal(restored.draft("crew", "leader"), "另一任务");
});
test("排队与立即插话属于当前对话，保存原话而不串到 leader", () => {
  const m = create(sample);
  m.draft("voice", "leader", "负责人未发草稿");
  m.send("voice", "engine", "稍后验证特殊边界", true);
  m.send("voice", "engine", "先调整当前方向");
  assert.equal(m.state.queues["voice:engine"][0].text, "稍后验证特殊边界");
  assert.equal(m.state.messages["voice:engine"][0].text, "先调整当前方向");
  assert.equal(m.draft("voice", "leader"), "负责人未发草稿");
  assert.equal(m.state.messages["voice:leader"], undefined);
});
test("停止成员只停本人，其他成员、leader 与其他任务继续", () => {
  const m = create(sample);
  m.stopConversation("voice", "engine");
  assert.equal(m.task("voice").members[0].state, "stopped");
  assert.equal(m.task("voice").members[1].state, "idle");
  assert.equal(m.state.stopped["voice:leader"], undefined);
  assert.equal(m.task("voice").state, "review");
  assert.equal(m.task("crew").state, "working");
});
test("整体停止包含成员；完成TODO或发送讨论消息不擅自恢复执行", () => {
  const m = create(sample);
  m.stopTask("voice");
  m.completeTodo("mic");
  m.send("voice", "engine", "讨论停止后的处理");
  assert.equal(m.task("voice").state, "stopped");
  for (const person of ["leader", "engine", "ui"])
    assert.equal(m.state.stopped["voice:" + person], true);
  assert.equal(m.state.conversations["voice:engine"], "stopped");
  assert.equal(m.task("crew").state, "working");
});
test("明确继续只恢复原先运行的执行，已交付成员不重做", () => {
  const m = create(sample);
  m.stopTask("voice");
  m.resumeTask("voice");
  assert.equal(m.task("voice").state, "review");
  assert.equal(m.task("voice").members[0].state, "working");
  assert.equal(m.task("voice").members[1].state, "idle");
});
test("成员此前手动停止，任务停止再继续也保持这个停止", () => {
  const m = create(sample);
  m.stopConversation("voice", "engine");
  m.stopTask("voice");
  m.resumeTask("voice");
  assert.equal(m.task("voice").members[0].state, "stopped");
  assert.equal(m.state.stopped["voice:engine"], true);
});
test("从对话形成任务，保留完整需求并关联唯一主工作区和负责人", () => {
  const m = create(sample);
  const text = "想做一个适合手机快速记录灵感的小工具";
  const id = m.newTask(text, "crew");
  assert.equal(m.task(id).goal, text);
  assert.equal(m.task(id).workspace, "crew");
  assert.equal(m.task(id).leader, "林序");
  assert.equal(m.state.messages[id + ":leader"][0].text, text);
  assert.equal(m.task(id).auto, false);
});
test("记下来只建TODO，保留原话/归属/模拟通知，完成不会新建任务", () => {
  const m = create(sample),
    count = m.state.tasks.length;
  const t = m.addTodo("以后想试的点子", "site");
  assert.equal(t.text, "以后想试的点子");
  assert.equal(t.workspace, "site");
  assert.equal(m.state.notifications[0].todo, t.id);
  m.completeTodo(t.id);
  assert.equal(m.state.tasks.length, count);
});
test("样例工作区与其任务一起持久化，不产生刷新后无法导航的孤立任务", () => {
  const m = create(sample);
  m.state.workspaces.push({ id: "folder", name: "普通文件夹" });
  const id = m.newTask("无需 Git 也能使用", "folder");
  const restored = create(sample, JSON.parse(JSON.stringify(m.state)));
  assert.ok(
    restored.state.workspaces.some((w) => w.id === restored.task(id).workspace),
  );
});
