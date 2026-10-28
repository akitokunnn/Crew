"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { create: createActions } = require("../ui-actions.js");
const flush = async () => {
  for (let i = 0; i < 5; i++)
    await new Promise((resolve) => setImmediate(resolve));
};
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const decode = (text) =>
  text.replace(
    /&(amp|lt|gt|quot|#39);/g,
    (_, key) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[key],
  );
const selection = {
  runtime: "codex",
  model: "haiku-named-native-model",
  effort: "native-effort",
};
const permission = { codex: "workspace-write", claude: "default" };
const keys = ["common", "leader", "member", "assistant", "report", "context"];
function fixture(custom) {
  // Each fixture represents a distinct server/browser lifetime. A recreation
  // within one fixture keeps the same public module and its in-memory drafts.
  delete require.cache[require.resolve("../scoped-settings.js")];
  const { create } = require("../scoped-settings.js");
  let html = "";
  const listeners = new Map(),
    requests = [],
    scopes = new Map(),
    prompts = new Map(),
    faults = [];
  const host = {
    classList: { add() {}, remove() {} },
    querySelector: () => null,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => {
      if (listeners.get(name) === fn) listeners.delete(name);
    },
  };
  Object.defineProperty(host, "innerHTML", {
    get: () => html,
    set: (value) => {
      html = value;
    },
  });
  const scope = (scope = "global", identity = "global") => {
    const id = JSON.stringify([scope, identity]);
    if (!scopes.has(id))
      scopes.set(id, {
        scope,
        identity,
        ai: {},
        execution: {},
        version: 1,
        applies: "next_launch",
      });
    return scopes.get(id);
  };
  const prompt = (key = "common", scope = "global", identity = "global") => {
    const id = JSON.stringify([key, scope, identity]);
    if (!prompts.has(id))
      prompts.set(id, {
        key,
        scope,
        identity,
        version: 1,
        content: null,
        overridden: false,
        effective: {
          key,
          content: `服务器 ${key} · ${scope} · ${identity}`,
          source: { scope: "default", identity: null },
          version: "builtin-v1",
        },
        applies: "next_turn",
      });
    return prompts.get(id);
  };
  async function request(path, body) {
    const url = new URL(path, "http://fixture"),
      search = url.searchParams;
    requests.push({
      path,
      body: body ? JSON.parse(JSON.stringify(body)) : null,
    });
    if (custom) {
      const handled = custom({ url, body, scope, prompt, requests });
      if (handled !== undefined) return handled;
    }
    if (url.pathname === "/api/runtimes")
      return {
        runtimes: [
          {
            id: "codex",
            name: "Codex",
            available: true,
            models: [
              {
                id: selection.model,
                name: "模型来自真实响应",
                status: "discovered",
                efforts: ["native-effort"],
              },
            ],
            efforts: ["native-effort"],
            discovery: { status: "discovered" },
          },
        ],
      };
    if (url.pathname === "/api/settings/scope") {
      const value = scope(
        body?.scope || search.get("scope"),
        body?.identity || search.get("identity"),
      );
      if (body) {
        Object.assign(value, { version: value.version + 1 });
        if (body.ai) Object.assign(value.ai, body.ai);
        if (body.execution) Object.assign(value.execution, body.execution);
      }
      return JSON.parse(JSON.stringify(value));
    }
    if (url.pathname === "/api/settings/effective")
      return {
        ai: { ...selection },
        execution: { ...permission },
        sources: {
          ai: Object.fromEntries(
            Object.keys(selection).map((field) => [
              field,
              {
                scope: "global",
                identity: "global",
                purpose: search.get("purpose") || "default",
              },
            ]),
          ),
          execution: Object.fromEntries(
            Object.keys(permission).map((field) => [
              field,
              { scope: "global", identity: "global" },
            ]),
          ),
        },
        applies: "next_launch",
        current_execution_changed: false,
      };
    if (url.pathname === "/api/prompts")
      return search.has("key")
        ? JSON.parse(
            JSON.stringify(
              prompt(
                search.get("key"),
                search.get("scope"),
                search.get("identity"),
              ),
            ),
          )
        : { prompts: keys.map((key) => ({ key })), applies: "next_turn" };
    if (
      url.pathname === "/api/prompts/save" ||
      url.pathname === "/api/prompts/reset"
    ) {
      const value = prompt(body.key, body.scope, body.identity);
      if (body.version !== value.version)
        throw Object.assign(Error("提示词已变化"), { status: 409 });
      value.version++;
      value.content = body.text ?? null;
      value.overridden = value.content !== null;
      value.effective = {
        key: body.key,
        content: body.text || "恢复后的上级内容",
        source: { scope: body.scope, identity: body.identity },
        version: value.version,
      };
      return JSON.parse(JSON.stringify(value));
    }
    if (url.pathname === "/api/prompts/applied")
      return {
        execution_id: search.get("execution_id"),
        applications: [
          {
            status: "sent",
            created_at: "2026-10-28T01:00:00Z",
            evidence: "宿主已发送",
            manifest: { prompts: [] },
          },
        ],
        loaded: "not_confirmed",
      };
    if (url.pathname === "/api/prompts/history")
      return { history: [], next_before: null };
    throw Error(`Unexpected endpoint ${path}`);
  }
  const actions = createActions({ onError: (error) => faults.push(error) }),
    legacy = [];
  const settingsOptions = {
    host,
    client: { request },
    actions,
    toast() {},
    onLegacySettings: (category) => legacy.push(category),
  };
  let module = create(settingsOptions);
  const emit = async (kind, dataset, value) => {
    listeners.get(kind)({ target: { dataset, value } });
    await flush();
  };
  return {
    host,
    get module() {
      return module;
    },
    requests,
    prompt,
    scope,
    faults,
    legacy,
    recreate() {
      module.destroy();
      module = create(settingsOptions);
    },
    open: async (next) => {
      await module.open(next);
      await flush();
    },
    dispatch: async (action, dataset = {}) => {
      actions.dispatch(action, { element: { dataset } });
      await flush();
    },
    change: (name, value) => emit("change", { ssChange: name }, value),
    fieldChange: (field, value) => emit("change", { ssField: field }, value),
    input: (field, value) => emit("input", { ssField: field }, value),
    text: () =>
      decode(
        (html.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/) || [])[1] || "",
      ),
    actions,
  };
}

test("scoped AI and permission changes use CAS patches and native catalog values", async () => {
  const f = fixture();
  await f.open({ project: "项目甲", sessionId: "member-1" });
  assert.match(f.host.innerHTML, /haiku-named-native-model/);
  assert.match(f.host.innerHTML, /native-effort/);
  assert.match(f.host.innerHTML, /下一次启动生效/);
  await f.change("purpose", "member");
  await f.input("model", "明确输入的模型");
  await f.dispatch("scoped-settings-save");
  let write = f.requests.filter((item) => item.body).at(-1);
  assert.equal(write.path, "/api/settings/scope");
  assert.deepEqual(write.body.ai, {
    member: { ...selection, model: "明确输入的模型" },
  });
  assert.equal(write.body.version, 1);
  assert.ok(write.body.request_id);
  await f.dispatch("scoped-settings-reset");
  write = f.requests.filter((item) => item.body).at(-1);
  assert.deepEqual(write.body.ai, { member: null });
  assert.equal(write.body.reset, undefined);
  await f.change("scope", "project");
  await f.dispatch("scoped-settings-tab", { tab: "execution" });
  await f.input("claude", "bypassPermissions");
  await f.dispatch("scoped-settings-save");
  write = f.requests.filter((item) => item.body).at(-1);
  assert.equal(write.body.scope, "project");
  assert.equal(write.body.identity, "项目甲");
  assert.deepEqual(write.body.execution, {
    ...permission,
    claude: "bypassPermissions",
  });
  assert.equal(f.faults.length, 0);
  f.module.destroy();
});

test("409 keeps the draft and requires explicit latest-version review before retry", async () => {
  const f = fixture();
  await f.open({ project: "项目甲", sessionId: "leader-1" });
  await f.dispatch("scoped-settings-tab", { tab: "prompts" });
  await f.change("scope", "global");
  await f.input("prompt", "用户原文 <保留> & 修改");
  const record = f.prompt();
  record.version = 2;
  record.effective.content = "另一端的新规则";
  await f.dispatch("scoped-settings-save");
  assert.equal(f.text(), "用户原文 <保留> & 修改");
  assert.match(f.host.innerHTML, /草稿保留/);
  await f.dispatch("scoped-settings-save");
  assert.equal(
    f.requests.filter((item) => item.path === "/api/prompts/save").length,
    1,
  );
  await f.dispatch("scoped-settings-reload");
  assert.equal(f.text(), "用户原文 <保留> & 修改");
  assert.match(f.host.innerHTML, /另一端的新规则/);
  await f.dispatch("scoped-settings-rebase");
  await f.dispatch("scoped-settings-save");
  const sent = f.requests.filter((item) => item.path === "/api/prompts/save");
  assert.equal(sent.at(-1).body.version, 2);
  assert.equal(sent.at(-1).body.text, "用户原文 <保留> & 修改");
  assert.notEqual(sent[0].body.request_id, sent[1].body.request_id);
  await f.change("scope", "project");
  await f.input("prompt", "工作区草稿");
  await f.change("scope", "session");
  await f.input("prompt", "单 AI 草稿");
  await f.change("scope", "project");
  assert.equal(f.text(), "工作区草稿");
  await f.change("scope", "session");
  assert.equal(f.text(), "单 AI 草稿");
  f.module.destroy();
});

test("prompt scope versions are separate from effective versions, and history/applied use actual endpoints", async () => {
  const f = fixture();
  await f.open({ project: "项目甲", sessionId: "leader-1" });
  await f.dispatch("scoped-settings-tab", { tab: "prompts" });
  await f.dispatch("scoped-settings-applied");
  assert.match(f.host.innerHTML, /没有明确的执行身份/);
  assert.equal(
    f.requests.some((item) => item.path.startsWith("/api/prompts/applied")),
    false,
  );
  await f.open({
    project: "项目甲",
    sessionId: "leader-1",
    executionId: "task-execution-1",
  });
  await f.dispatch("scoped-settings-applied");
  assert.match(f.host.innerHTML, /加载未确认/);
  assert.match(f.host.innerHTML, /宿主已发送，加载待确认/);
  assert.ok(
    f.requests.some(
      (item) =>
        item.path === "/api/prompts/applied?execution_id=task-execution-1",
    ),
  );
  await f.dispatch("scoped-settings-history");
  assert.match(f.host.innerHTML, /还没有修改记录/);
  assert.ok(
    f.requests.some((item) => item.path.startsWith("/api/prompts/history?")),
  );
  await f.input("prompt", "新的规则");
  await f.dispatch("scoped-settings-save");
  assert.equal(
    f.requests.find((item) => item.path === "/api/prompts/save").body.version,
    1,
  );
  await f.dispatch("scoped-settings-tab", { tab: "more" });
  await f.dispatch("scoped-settings-legacy", { category: "remote" });
  assert.deepEqual(f.legacy, ["remote"]);
  f.module.destroy();
});

test("late reads cannot overwrite a newer context, and destroy releases actions and listeners", async () => {
  const first = deferred(),
    second = deferred();
  let reads = 0;
  const f = fixture(({ url }) => {
    if (url.pathname === "/api/settings/effective")
      return ++reads === 1 ? first.promise : second.promise;
  });
  const one = f.module.open({ project: "旧项目" }),
    two = f.module.open({ project: "新项目" });
  const effective = (model) => ({
    ai: { ...selection, model },
    execution: permission,
    sources: { ai: {}, execution: {} },
    applies: "next_launch",
  });
  second.resolve(effective("新上下文"));
  await two;
  first.resolve(effective("旧上下文"));
  await one;
  assert.match(f.host.innerHTML, /新上下文/);
  assert.doesNotMatch(f.host.innerHTML, /旧上下文/);
  f.module.destroy();
  const prior = f.host.innerHTML;
  assert.equal(f.actions.dispatch("scoped-settings-save", {}), false);
  assert.equal(f.host.innerHTML, prior);
  await assert.rejects(f.module.open(), /destroyed/);
});

test("route destroy/recreate preserves non-secret drafts and history reading position", async () => {
  const f = fixture();
  const context = {
    project: "项目甲",
    sessionId: "leader-1",
    executionId: "execution-1",
  };
  await f.open(context);
  await f.dispatch("scoped-settings-tab", { tab: "prompts" });
  await f.change("scope", "session");
  await f.input("prompt", "离页后仍保留的草稿");
  await f.dispatch("scoped-settings-history");
  f.host.scrollTop = 170;
  f.recreate();
  await f.open(context);
  assert.equal(f.text(), "离页后仍保留的草稿");
  assert.match(f.host.innerHTML, /提示词修改历史/);
  assert.equal(f.host.scrollTop, 170);
  f.module.destroy();
});

test("unknown save results retain the same request identity for an explicit retry", async () => {
  let reject = true;
  const f = fixture(({ url, body }) => {
    if (url.pathname === "/api/prompts/save" && reject) {
      reject = false;
      throw Error("网络中断，结果未知");
    }
  });
  await f.open();
  await f.dispatch("scoped-settings-tab", { tab: "prompts" });
  await f.input("prompt", "稳定请求内容");
  await f.dispatch("scoped-settings-save");
  assert.match(f.host.innerHTML, /保存结果未确认/);
  await f.dispatch("scoped-settings-save");
  const writes = f.requests.filter((item) => item.path === "/api/prompts/save");
  assert.equal(writes.length, 2);
  assert.equal(writes[0].body.request_id, writes[1].body.request_id);
  f.module.destroy();
});

test("an unavailable history endpoint reports its real failure without synthetic records", async () => {
  const f = fixture(({ url }) => {
    if (url.pathname === "/api/prompts/history")
      throw Object.assign(Error("该后端尚未提供历史"), { status: 404 });
  });
  await f.open();
  await f.dispatch("scoped-settings-tab", { tab: "prompts" });
  await f.dispatch("scoped-settings-history");
  assert.match(f.host.innerHTML, /后端暂未提供历史接口：该后端尚未提供历史/);
  assert.doesNotMatch(f.host.innerHTML, /这个范围还没有修改记录/);
  f.module.destroy();
});

test("carrier switches clear only the current scope's old model/effort, before saving", async (t) => {
  const cases = [
    ["global", "claude", "codex"],
    ["global", "codex", "claude"],
    ["project", "claude", "codex"],
    ["session", "codex", "claude"],
    ["project", "claude", null],
    ["session", "codex", null],
  ];
  for (const [scope, from, to] of cases) {
    for (const explicit of [false, true]) {
      await t.test(`${scope}: ${from} → ${to || "原生默认"}${explicit ? "，保留后续明确选择" : "，保存原生默认"}`, async () => {
        const initial = { runtime: from, model: `${from}-old-model`, effort: `${from}-old-effort` };
        const source = { scope, identity: scope === "global" ? "global" : scope === "project" ? "项目甲" : "member-1" };
        const f = fixture(({ url }) => {
          if (url.pathname === "/api/settings/effective") return {
            ai: { ...initial }, execution: { ...permission },
            sources: { ai: Object.fromEntries(Object.keys(initial).map(field => [field, source])), execution: {} },
            applies: "next_launch", current_execution_changed: false,
          };
        });
        await f.open({ project: "项目甲", sessionId: "member-1" });
        await f.change("purpose", "member");
        await f.change("scope", scope);
        await f.input("runtime", to || "");
        assert.match(f.host.innerHTML, /data-ss-field="model"[^>]*value=""/);
        assert.match(f.host.innerHTML, /data-ss-field="effort"[^>]*value=""/);
        assert.match(f.host.innerHTML, /下一次启动生效/);
        const expected = { runtime: to, model: null, effort: null };
        if (explicit) {
          expected.model = `${to || "native"}-explicit-model`;
          expected.effort = `${to || "native"}-explicit-effort`;
          await f.input("model", expected.model);
          await f.input("effort", expected.effort);
        }
        // A select emits both input/change. Repeating the same carrier must not
        // erase explicit picks after the actual carrier transition.
        await f.fieldChange("runtime", to || "");
        await f.dispatch("scoped-settings-save");
        const body = f.requests.filter(item => item.path === "/api/settings/scope" && item.body).at(-1)?.body;
        assert.ok(body, "The explicit save should reach the scoped settings endpoint");
        assert.equal(body.scope, scope);
        assert.equal(body.identity, source.identity);
        assert.deepEqual(body.ai, { member: expected });
        assert.equal(body.version, 1);
        assert.equal(f.faults.length, 0);
        f.module.destroy();
      });
    }
  }
});

test("switching a carrier preserves independent project/session drafts and their CAS conflict", async () => {
  const f = fixture();
  await f.open({ project: "项目甲", sessionId: "member-1" });
  await f.change("purpose", "member");
  await f.change("scope", "project");
  await f.input("model", "工作区尚未保存的模型");
  await f.input("effort", "工作区明确强度");
  await f.change("scope", "session");
  await f.fieldChange("runtime", "claude");
  await f.input("model", "单AI切换后的模型");
  await f.input("effort", "单AI明确强度");
  await f.change("scope", "project");
  assert.match(f.host.innerHTML, /value="工作区尚未保存的模型"/);
  assert.match(f.host.innerHTML, /value="工作区明确强度"/);
  await f.change("scope", "session");
  assert.match(f.host.innerHTML, /value="单AI切换后的模型"/);
  assert.match(f.host.innerHTML, /value="单AI明确强度"/);
  f.scope("session", "member-1").version = 2;
  await f.dispatch("scoped-settings-reload");
  assert.match(f.host.innerHTML, /服务器版本已变化，草稿仍保留/);
  await f.dispatch("scoped-settings-save");
  assert.equal(f.requests.some(item => item.path === "/api/settings/scope" && item.body), false);
  await f.dispatch("scoped-settings-rebase");
  await f.dispatch("scoped-settings-save");
  const body = f.requests.filter(item => item.path === "/api/settings/scope" && item.body).at(-1).body;
  assert.equal(body.version, 2);
  assert.deepEqual(body.ai, { member: { runtime: "claude", model: "单AI切换后的模型", effort: "单AI明确强度" } });
  f.module.destroy();
});

test("opening a different workspace or AI targets that object rather than the last global editor", async () => {
  const f = fixture();
  const selectedScope = () => {
    const read = f.requests.filter(item => item.path.startsWith("/api/settings/scope?") && !item.body).at(-1);
    const params = new URL(read.path,"http://fixture").searchParams;
    return [params.get("scope"),params.get("identity")];
  };
  await f.open({project:"项目甲"});
  assert.deepEqual(selectedScope(),["project","项目甲"]);
  await f.change("scope","global");
  await f.input("model","保留的全局草稿");
  await f.open({project:"项目乙"});
  assert.deepEqual(selectedScope(),["project","项目乙"]);
  await f.open({project:"项目甲",sessionId:"member-1",sessionRole:"member"});
  assert.deepEqual(selectedScope(),["session","member-1"]);
  await f.change("scope","global");
  await f.open({project:"项目甲"});
  assert.deepEqual(selectedScope(),["project","项目甲"]);
  f.module.destroy();
});
