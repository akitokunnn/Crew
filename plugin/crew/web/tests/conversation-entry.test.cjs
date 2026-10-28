"use strict";
const { test } = require("node:test"),
  assert = require("node:assert/strict"),
  { create } = require("../conversation-entry.js");
const task = {
  id: "task-a",
  primary_project: "alpha",
  leader_session_id: "leader-a",
};
function fixture(respond) {
  const memory = new Map(),
    calls = [];
  let counter = 0;
  const options = {
    requestId: () => "id-" + ++counter,
    remember: (key, value) =>
      memory.set(key, value == null ? null : structuredClone(value)),
    recall: (key, fallback) =>
      memory.has(key) ? structuredClone(memory.get(key)) : fallback,
    client: {
      request: async (path, body) => {
        calls.push({ path, body: structuredClone(body) });
        return respond(path, body, calls);
      },
    },
  };
  return { calls, memory, options, module: create(options) };
}
test("task creation and first message use separate stable receipts across uncertain send", async () => {
  let lost = true;
  const f = fixture(async (path) => {
    if (path === "/api/tasks/create") return { task };
    if (lost) {
      lost = false;
      throw TypeError("connection lost");
    }
    return { accepted: true };
  });
  const payload = { project: "alpha", text: "保留原话" };
  await assert.rejects(f.module.start(payload), /connection lost/);
  f.module = create(f.options);
  await f.module.start(payload);
  assert.equal(
    f.calls.filter((item) => item.path === "/api/tasks/create").length,
    1,
  );
  const sends = f.calls.filter((item) =>
    item.path.startsWith("/api/chat/send"),
  );
  assert.equal(sends.length, 2);
  assert.equal(sends[0].body.message_id, sends[1].body.message_id);
  assert.equal(sends[0].body.intent, "dialogue");
  assert.equal(f.memory.get("conversation.create"), null);
});
test("submitted and unknown receipts keep the original attempt rather than pretending acceptance", async () => {
  for (const receipt of [
    { status: "submitted" },
    { status: "unknown", accepted: false },
    {},
  ]) {
    const f = fixture(async (path) =>
      path === "/api/tasks/create" ? { task } : receipt,
    );
    await assert.rejects(f.module.start({ project: "alpha", text: "hello" }));
    assert.ok(f.memory.get("conversation.create")?.message_id);
  }
});
test("item chat includes explicit task selection and server conversation ownership", async () => {
  const f = fixture(async (path) =>
    path === "/api/personal/items/chat"
      ? {
          task: { ...task, id: "task-b", primary_project: "beta" },
          conversation: {
            project: "beta",
            goal_id: "task-b",
            session_id: "leader-b",
            text: "原文",
          },
        }
      : { accepted: true },
  );
  await f.module.fromItem(
    { id: "item", version: 2, project: "alpha" },
    undefined,
    { task_id: "task-b" },
  );
  assert.equal(f.calls[0].body.task_id, "task-b");
  assert.equal(f.calls[1].path, "/api/chat/send?project=beta");
  assert.equal(f.calls[1].body.session_id, "leader-b");
  assert.equal(f.calls[1].body.text, "原文");
});
test("item metadata version refresh cannot cause another first message after unknown delivery", async () => {
  let unknown = true;
  const f = fixture(async (path) =>
    path === "/api/personal/items/chat"
      ? {
          task,
          conversation: {
            project: "alpha",
            goal_id: task.id,
            session_id: task.leader_session_id,
            text: "原文",
          },
        }
      : unknown
        ? { status: "unknown", accepted: false }
        : { accepted: true },
  );
  await assert.rejects(
    f.module.fromItem({ id: "item", version: 1, project: "alpha" }),
  );
  unknown = false;
  f.module = create(f.options);
  await f.module.fromItem({ id: "item", version: 2, project: "alpha" });
  assert.equal(
    f.calls.filter((item) => item.path === "/api/personal/items/chat").length,
    1,
  );
  const sends = f.calls.filter((item) =>
    item.path.startsWith("/api/chat/send"),
  );
  assert.equal(sends[0].body.message_id, sends[1].body.message_id);
});
