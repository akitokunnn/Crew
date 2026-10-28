"use strict";
const { test } = require("node:test"),
  assert = require("node:assert/strict");
const { create } = require("../personal-workspace.js"),
  { create: createActions } = require("../ui-actions.js"),
  conversation = require("../conversation-entry.js");
const clone = (value) => JSON.parse(JSON.stringify(value));
const error = (message, status) => Object.assign(Error(message), { status });
function fixture({ before, after } = {}) {
  const memory = new Map(),
    requests = [],
    listeners = new Map(),
    navigations = [],
    notices = [],
    receipts = new Map();
  const state = {
    items: [
      {
        id: "idea",
        text: "原始灵感",
        project: "alpha",
        tags: ["灵感"],
        task_ids: [],
        state: "open",
        version: 1,
      },
    ],
    todos: [
      {
        id: 7,
        title: "体验成果",
        project: "alpha",
        projects: ["alpha"],
        task_ids: ["task-a"],
        tags: ["待体验"],
        version: 4,
        metadata_version: 0,
        status: "open",
      },
    ],
    tasks: [
      {
        id: "task-a",
        title: "任务甲",
        primary_project: "alpha",
        leader_session_id: "leader-a",
        state: "running",
      },
      {
        id: "task-b",
        title: "任务乙",
        primary_project: "beta",
        leader_session_id: "leader-b",
        state: "running",
      },
    ],
    uploads: [],
  };
  const ui = {
    escape: (value) =>
      String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll('"', "&quot;"),
    requestId: () => crypto.randomUUID(),
    remember: (key, value) => memory.set(key, clone(value)),
    recall: (key, fallback) =>
      memory.has(key) ? clone(memory.get(key)) : fallback,
  };
  const host = {
    innerHTML: "",
    page: { scrollTop: 0 },
    querySelector(selector) {
      return selector === ".wb-page" ? this.page : null;
    },
    addEventListener: (type, handler) => listeners.set(type, handler),
    removeEventListener: (type, handler) => {
      if (listeners.get(type) === handler) listeners.delete(type);
    },
  };
  async function request(path, body) {
    requests.push({ path, body: body ? clone(body) : undefined });
    const url = new URL(path, "http://fixture");
    if (before) await before({ path: url.pathname, body, state, requests });
    const fingerprint = JSON.stringify(body),
      prior = body?.request_id && receipts.get(url.pathname + body.request_id);
    if (prior) {
      if (prior.fingerprint !== fingerprint) throw error("请求编号冲突", 409);
      return clone(prior.result);
    }
    let result;
    if (url.pathname === "/api/personal/items")
      result = { items: clone(state.items), todos: clone(state.todos) };
    else if (url.pathname === "/api/tasks")
      result = { tasks: clone(state.tasks) };
    else if (url.pathname === "/api/personal/capture") {
      const item = {
        id: "idea-" + state.items.length,
        ...body,
        state: "open",
        version: 1,
      };
      state.items.push(item);
      result = { item };
    } else if (url.pathname === "/api/personal/update") {
      const item = state.items.find((item) => item.id === body.id);
      if (item.version !== body.version) throw error("记录已变化", 409);
      for (const field of ["text", "project", "tags", "state"])
        if (field in body) item[field] = clone(body[field]);
      item.version++;
      result = { item };
    } else if (url.pathname === "/api/personal/items/link") {
      const item = state.items.find((item) => item.id === body.id);
      if (item.version !== body.version) throw error("记录已变化", 409);
      item.task_ids = [...new Set([...item.task_ids, body.task_id])];
      item.version++;
      result = {
        item,
        task: state.tasks.find((task) => task.id === body.task_id),
      };
    } else if (url.pathname === "/api/personal/items/chat") {
      const item = state.items.find((item) => item.id === body.id),
        task = state.tasks.find(
          (task) => task.id === (body.task_id || item.task_ids[0]),
        );
      result = {
        item,
        task,
        conversation: {
          project: task.primary_project,
          goal_id: task.id,
          session_id: task.leader_session_id,
          text: item.text,
        },
      };
    } else if (url.pathname === "/api/chat/send")
      result = { accepted: true, status: "accepted" };
    else if (url.pathname === "/api/personal/todos/update") {
      const todo = state.todos.find((todo) => todo.id === body.id);
      if (todo.metadata_version !== body.metadata_version)
        throw error("标签已变化", 409);
      todo.tags = body.tags;
      todo.metadata_version++;
      result = { todo };
    } else if (url.pathname === "/api/tasks/attachments") {
      const attachment = {
        id: "attachment-" + (state.uploads.length + 1),
        filename: body.filename,
      };
      state.uploads.push(attachment);
      result = { attachment };
    } else if (url.pathname === "/api/personal/todos/complete") {
      const todo = state.todos.find((todo) => todo.id === body.id);
      if (todo.version !== body.version) throw error("TODO 已变化", 409);
      todo.version++;
      todo.status = body.status;
      todo.evidence = body.attachments;
      result = { todo };
    } else throw Error("Unexpected endpoint " + path);
    if (body?.request_id)
      receipts.set(url.pathname + body.request_id, {
        fingerprint,
        result: clone(result),
      });
    if (after)
      await after({ path: url.pathname, body, state, requests, result });
    return clone(result);
  }
  const actions = createActions(),
    options = {
      host,
      client: { request },
      actions,
      navigate: (value) => navigations.push(value),
      toast: (text) => notices.push(text),
      projects: () => [{ name: "alpha" }, { name: "beta" }],
      ui,
      conversationFactory: conversation.create,
    };
  let module = create(options);
  return {
    host,
    ui,
    state,
    requests,
    listeners,
    navigations,
    notices,
    actions,
    get module() {
      return module;
    },
    recreate() {
      module.destroy();
      module = create(options);
    },
    open: () => module.open(),
    writes: (path) =>
      requests.filter((item) => item.path === path && item.body),
    dispatch: async (action, dataset) => {
      actions.dispatch(action, { element: { dataset } });
      for (let i = 0; i < 4; i++)
        await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

test("capture retries one request after an accepted response is lost, including recreation", async () => {
  let lost = true;
  const f = fixture({
    after: ({ path }) => {
      if (path === "/api/personal/capture" && lost) {
        lost = false;
        throw TypeError("response lost");
      }
    },
  });
  await f.open();
  const payload = { text: "  保留原话  ", project: null };
  await assert.rejects(f.module.capture(payload), /response lost/);
  f.recreate();
  await f.open();
  await f.module.capture(payload);
  const writes = f.writes("/api/personal/capture");
  assert.equal(writes.length, 2);
  assert.equal(writes[0].body.request_id, writes[1].body.request_id);
  assert.equal(
    f.state.items.filter((item) => item.text === payload.text).length,
    1,
  );
  assert.equal(f.ui.recall("personal.capture"), "");
  f.module.destroy();
});

test("record edits retain their base version across refresh/409 and require explicit rebase", async () => {
  const f = fixture();
  await f.open();
  f.module.setItemDraft("idea", { text: "我的草稿" });
  f.state.items[0].text = "另一端更新";
  f.state.items[0].version = 2;
  await assert.rejects(
    f.module.saveItem("idea"),
    (error) => error.status === 409,
  );
  assert.equal(f.writes("/api/personal/update")[0].body.version, 1);
  f.recreate();
  await f.open();
  await assert.rejects(f.module.saveItem("idea"), /明确选择/);
  assert.equal(f.writes("/api/personal/update").length, 1);
  assert.equal(f.ui.recall("personal.edit.idea").text, "我的草稿");
  assert.equal(f.ui.recall("personal.edit.idea").version, 1);
  f.module.rebaseItem("idea");
  await f.module.saveItem("idea");
  assert.equal(f.state.items[0].version, 3);
  assert.equal(f.state.items[0].text, "我的草稿");
  f.module.destroy();
});

test("TODO label edits use independent metadata CAS without changing completion version", async () => {
  const f = fixture();
  await f.open();
  f.module.setTodoDraft(7, { tags: "体验, 截图，体验" });
  await f.module.saveTodoTags(7);
  const write = f.writes("/api/personal/todos/update")[0];
  assert.equal(write.body.metadata_version, 0);
  assert.equal("version" in write.body, false);
  assert.deepEqual(write.body.tags, ["体验", "截图"]);
  assert.equal(f.state.todos[0].version, 4);
  assert.equal(f.state.todos[0].metadata_version, 1);
  f.module.destroy();
});

test("tag conflicts preserve the original metadata version until the explicit rebase action", async () => {
  const f = fixture();
  await f.open();
  f.module.setTodoDraft(7, { tags: "我的标签" });
  f.state.todos[0].metadata_version = 2;
  await assert.rejects(
    f.module.saveTodoTags(7),
    (error) => error.status === 409,
  );
  await f.module.refresh();
  await assert.rejects(f.module.saveTodoTags(7), /明确选择/);
  assert.equal(f.writes("/api/personal/todos/update").length, 1);
  await f.dispatch("personal-todo-tags-rebase", { id: "7" });
  await f.module.saveTodoTags(7);
  assert.equal(
    f.writes("/api/personal/todos/update").at(-1).body.metadata_version,
    2,
  );
  assert.equal(f.state.todos[0].version, 4);
  f.module.destroy();
});

test("successful saves retain text typed while the original write was in flight", async () => {
  let f;
  f = fixture({
    after: ({ path }) => {
      if (path === "/api/personal/capture")
        f.ui.remember("personal.capture", "下一条灵感");
      if (path === "/api/personal/update")
        f.module.setItemDraft("idea", { text: "保存中的继续编辑" });
    },
  });
  await f.open();
  await f.module.capture({ text: "第一条灵感", project: "alpha" });
  assert.equal(f.ui.recall("personal.capture"), "下一条灵感");
  f.module.setItemDraft("idea", { text: "提交的文字" });
  await f.module.saveItem("idea");
  const draft = f.ui.recall("personal.edit.idea");
  assert.equal(draft.text, "保存中的继续编辑");
  assert.equal(draft.version, 2);
  assert.equal(f.state.items[0].text, "提交的文字");
  f.module.destroy();
});

test("linking another task cannot silently rebase an older content draft", async () => {
  const f = fixture();
  await f.open();
  f.module.setItemDraft("idea", { text: "旧版本草稿" });
  f.state.items[0].version = 2;
  f.state.items[0].text = "服务器新内容";
  await f.module.refresh();
  await f.module.linkItem("idea", "task-b");
  assert.equal(f.ui.recall("personal.edit.idea").version, 1);
  await assert.rejects(f.module.saveItem("idea"), /明确选择/);
  assert.equal(f.state.items[0].text, "服务器新内容");
  f.module.destroy();
});

test("archive remains recoverable in the same list and loading requests include inactive records", async () => {
  const f = fixture();
  await f.open();
  await f.module.archiveItem("idea", "archived");
  assert.equal(f.state.items[0].state, "archived");
  assert.doesNotMatch(f.host.innerHTML, /原始灵感/);
  f.module.setVisibility("archived");
  assert.match(f.host.innerHTML, /原始灵感/);
  assert.match(f.host.innerHTML, /恢复到清单/);
  await f.module.archiveItem("idea", "open");
  f.module.setVisibility("open");
  assert.match(f.host.innerHTML, /原始灵感/);
  assert.ok(
    f.requests
      .filter((item) => item.path.startsWith("/api/personal/items?"))
      .every(
        (item) =>
          new URL(item.path, "http://fixture").searchParams.get(
            "include_inactive",
          ) === "1",
      ),
  );
  f.module.destroy();
});

test("linking uses available task identities and multi-task chat requires explicit selection", async () => {
  const f = fixture();
  f.state.items[0].task_ids = ["task-a"];
  await f.open();
  await assert.rejects(f.module.linkItem("idea", "not-real"), /可用的真实任务/);
  await f.module.linkItem("idea", "task-b");
  assert.deepEqual(f.state.items[0].task_ids, ["task-a", "task-b"]);
  const before = f.requests.length;
  assert.equal((await f.module.openItemChat("idea")).selection_required, true);
  assert.equal(f.requests.length, before);
  assert.match(f.host.innerHTML, /选择要继续的对话/);
  await f.module.openItemChat("idea", "task-b");
  assert.equal(f.writes("/api/personal/items/chat")[0].body.task_id, "task-b");
  assert.equal(f.navigations.at(-1).taskId, "task-b");
  f.module.destroy();
});

test("successful attachment uploads survive failed completion and are not uploaded again", async () => {
  let fail = true;
  const f = fixture({
    before: ({ path }) => {
      if (path === "/api/personal/todos/complete" && fail) {
        fail = false;
        throw TypeError("completion unknown");
      }
    },
  });
  await f.open();
  const file = new File(["图片证据内容"], "evidence.txt", {
    type: "text/plain",
  });
  await assert.rejects(
    f.module.completeTodo("7", "done", { note: "已体验", files: [file] }),
    /completion unknown/,
  );
  assert.equal(f.state.uploads.length, 1);
  const first = f.writes("/api/personal/todos/complete")[0].body;
  f.recreate();
  await f.open();
  await f.module.completeTodo("7", "done", { note: "已体验", files: [file] });
  assert.equal(f.writes("/api/tasks/attachments").length, 1);
  assert.equal(
    f.writes("/api/personal/todos/complete")[1].body.request_id,
    first.request_id,
  );
  assert.deepEqual(f.state.todos[0].evidence, [
    { goal_id: "task-a", attachment_id: "attachment-1" },
  ]);
  f.module.destroy();
});

test("unknown upload retries the original bytes and identity without reselecting a file", async () => {
  let lost = true;
  const f = fixture({
    after: ({ path }) => {
      if (path === "/api/tasks/attachments" && lost) {
        lost = false;
        throw TypeError("upload response lost");
      }
    },
  });
  await f.open();
  await assert.rejects(
    f.module.completeTodo("7", "done", {
      note: "证据",
      files: [new File(["proof"], "proof.txt")],
    }),
    /upload response lost/,
  );
  f.recreate();
  await f.open();
  await f.module.completeTodo("7", "done", { note: "证据" });
  const uploads = f.writes("/api/tasks/attachments");
  assert.equal(uploads.length, 2);
  assert.deepEqual(uploads[0].body, uploads[1].body);
  assert.equal(f.state.uploads.length, 1);
  f.module.destroy();
});

test("cancellation requires a reason before uploads; destroy releases feature actions/listeners", async () => {
  const f = fixture();
  await f.open();
  await assert.rejects(
    f.module.completeTodo("7", "cancelled", {
      note: "",
      files: [new File(["unused"], "unused.txt")],
    }),
    /说明/,
  );
  assert.equal(f.writes("/api/tasks/attachments").length, 0);
  f.module.destroy();
  assert.equal(f.listeners.size, 0);
  assert.equal(f.actions.dispatch("personal-retry"), false);
});
