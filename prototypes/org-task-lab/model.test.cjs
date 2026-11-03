"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const sample = require("./data.js");
const { project, conversationKey } = require("./model.js");

test("同一个成员在多个任务保留独立分工与执行状态", () => {
  const group = project(sample, "lin");
  const links = group.links.filter(link => link.personId === "zhi" && !link.history);
  assert.deepEqual(links.map(link => [link.taskId, link.state]), [["org", "working"], ["chat", "queued"]]);
  assert.equal(links[0].executionId, "e-zhi");
  assert.ok(group.links.some(link => link.personId === "lin" && link.state === "working"));
});

test("旧要求版本的执行不能冒充当前分工状态", () => {
  const copy = structuredClone(sample);
  copy.goals.find(goal => goal.id === "org").requirement_version = 3;
  const group = project(copy, "lin");
  assert.equal(group.links.find(link => link.personId === "zhi" && link.taskId === "org").state, "planned");
});

test("交接与已完成任务进入历史，待命人员与其他组保持独立", () => {
  const group = project(sample, "lin");
  assert.ok(group.links.find(link => link.personId === "shi" && link.taskId === "org").history);
  assert.ok(group.links.find(link => link.personId === "yan").history);
  assert.ok(group.people.some(person => person.id === "qing"));
  assert.equal(group.links.some(link => link.personId === "qing"), false);
  assert.equal(group.people.some(person => person.id === "wu"), false);
  assert.equal(project(sample, "jian").links.find(link => link.personId === "xu").state, "planned");
});

test("总会话与两项任务对话的稳定身份互不相同", () => {
  const keys = [conversationKey("zhi"), conversationKey("zhi", "org"), conversationKey("zhi", "chat")];
  assert.equal(new Set(keys).size, 3);
  assert.deepEqual(JSON.parse(keys[0]), ["zhi", null]);
});

test("新建小组保留leader且任务与关系为零", () => {
  const group = project(sample, "shou");
  assert.equal(group.people.length, 1);
  assert.equal(group.leader.id, "shou");
  assert.deepEqual(group.tasks, []);
  assert.deepEqual(group.links, []);
});

test("真实暂停、离线与验收状态有明确投影，失败与取消任务归历史", () => {
  for (const status of ["paused", "pausing", "waiting_user", "idle", "offline", "awaiting_acceptance", "cancelled"]) {
    const copy = structuredClone(sample);
    copy.executions.find(execution => execution.id === "e-zhi").status = status;
    assert.notEqual(project(copy, "lin").links.find(link => link.executionId === "e-zhi").state, "unknown");
  }
  for (const status of ["completed", "failed", "cancelled"]) {
    const copy = structuredClone(sample);
    copy.goals.find(goal => goal.id === "org").status = status;
    assert.ok(project(copy, "lin").links.filter(link => link.taskId === "org").every(link => link.history));
  }
});

test("leader统筹每个任务，只在有独立执行时进入执行人员分工", () => {
  const group = project(sample, "lin");
  assert.ok(group.tasks.every(task => task.assignments.some(assignment => assignment.session_id === "lin")));
  assert.deepEqual(group.links.filter(link => link.personId === "lin").map(link => link.taskId), ["org"]);
});
