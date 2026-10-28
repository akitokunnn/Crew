"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "settings.js"), "utf8");
const decode = (text) =>
  text.replace(
    /&(amp|lt|gt|quot|#39);/g,
    (_, entity) =>
      ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[entity],
  );

function runtime(storage = new Map()) {
  const document = { documentElement: { dataset: {} } };
  const windowListeners = new Map();
  const window = {
    matchMedia: () => ({ matches: false }),
    addEventListener: (name, callback) => windowListeners.set(name, callback),
    removeEventListener: (name, callback) => {
      if (windowListeners.get(name) === callback) windowListeners.delete(name);
    },
    dispatchEvent: (event) => windowListeners.get(event.type)?.(event),
  };
  const localStorage = {
    getItem: (key) => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value),
  };
  class CustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  }
  vm.runInNewContext(
    source,
    { window, document, localStorage, CustomEvent, Date },
    { filename: "settings.js" },
  );

  function open(workspaceId = "workspace-a", taskId = "task-a") {
    const listeners = new Map();
    let html = "",
      editor = null,
      controls = new Map(),
      closed = false;
    const host = {
      addEventListener: (name, callback) => listeners.set(name, callback),
      removeEventListener: (name, callback) => {
        if (listeners.get(name) === callback) listeners.delete(name);
      },
      contains: () => true,
      querySelector: (selector) =>
        selector === "#prototype-prompt-editor"
          ? editor
          : controls.get(selector) || null,
    };
    // The test host models only the editable textarea/select surface used by the public renderer.
    Object.defineProperty(host, "innerHTML", {
      get: () => html,
      set(value) {
        html = value;
        const textarea = html.match(
          /<textarea id="prototype-prompt-editor"[^>]*>([\s\S]*?)<\/textarea>/,
        );
        editor = textarea
          ? { id: "prototype-prompt-editor", value: decode(textarea[1]) }
          : null;
        controls = new Map();
        for (const select of html.matchAll(
          /<select id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g,
        )) {
          const selected = select[2].match(/<option value="([^"]+)" selected/);
          controls.set(`#${select[1]}`, {
            id: select[1],
            value: selected ? decode(selected[1]) : "",
          });
        }
      },
    });
    const cleanup = window.PrototypeSettings.render(host, {
      workspaceId,
      workspaceName: workspaceId,
      taskId,
      taskTitle: taskId,
      members: [
        { id: "worker-a", name: "成员甲" },
        { id: "worker-b", name: "成员乙" },
      ],
      toast() {},
      close() {
        closed = true;
      },
      embedded: true,
    });
    return {
      cleanup,
      html: () => html,
      isClosed: () => closed,
      selection: (id) => controls.get(`#${id}`)?.value,
      text: () => {
        assert.ok(editor, "The prompt editor must be rendered");
        return editor.value;
      },
      click(action, value) {
        assert.ok(
          html.includes(`data-ps="${action}"`),
          `The ${action} action must be visible`,
        );
        listeners.get("click")({
          target: { closest: () => ({ dataset: { ps: action, value } }) },
        });
      },
      change(id, value) {
        const control = controls.get(`#${id}`);
        assert.ok(control, `The ${id} select must be rendered`);
        control.value = value;
        listeners.get("change")({ target: control });
      },
      edit(value) {
        assert.ok(editor, "The prompt editor must be rendered");
        editor.value = value;
        listeners.get("input")({ target: editor });
      },
      loadedTexts: () =>
        [
          ...html.matchAll(
            /<details class="ps-loaded">[\s\S]*?<pre>([\s\S]*?)<\/pre><\/details>/g,
          ),
        ].map((match) => decode(match[1])),
    };
  }
  return { open, settings: window.PrototypeSettings, document, storage };
}

function prompts(view) {
  view.click("section", "prompts");
  return view;
}
function saveText(view, text) {
  view.edit(text);
  view.click("save-prompt");
}
function singleAI(view, target = "leader") {
  view.change("ps-scope", "ai");
  view.change("ps-ai-target", target);
}

test("global defaults inherit while workspace, task and individual AI overrides stay isolated", () => {
  const app = runtime();
  let view = prompts(app.open());
  saveText(view, "全局原则");
  view.change("ps-scope", "workspace");
  assert.equal(view.text(), "全局原则");
  saveText(view, "工作区 A 原则");
  singleAI(view);
  saveText(view, "任务 A leader 原则");
  view.change("ps-ai-target", "worker-a");
  assert.equal(view.text(), "工作区 A 原则");
  saveText(view, "成员甲独立原则");
  view.cleanup();

  view = prompts(app.open("workspace-b", "task-b"));
  view.change("ps-scope", "workspace");
  assert.equal(view.text(), "全局原则");
  saveText(view, "工作区 B 原则");
  view.cleanup();

  view = prompts(app.open("workspace-a", "another-task"));
  singleAI(view);
  assert.equal(view.text(), "工作区 A 原则");
  view.cleanup();

  view = prompts(app.open());
  singleAI(view);
  assert.equal(view.text(), "任务 A leader 原则");
  view.change("ps-ai-target", "worker-a");
  assert.equal(view.text(), "成员甲独立原则");
  view.change("ps-ai-target", "worker-b");
  assert.equal(view.text(), "工作区 A 原则");
  view.change("ps-ai-target", "worker-a");
  view.click("reset-prompt");
  assert.equal(view.text(), "工作区 A 原则");
  view.cleanup();
});

test("leaving settings preserves unsaved prompt and selection without saving the configuration", () => {
  const app = runtime();
  let view = prompts(app.open());
  singleAI(view, "worker-a");
  view.change("ps-prompt-role", "report");
  const original = view.text();
  view.edit("未保存 <保留> & 样例");
  view.click("close");
  assert.equal(view.isClosed(), true);
  view.cleanup();

  view = app.open();
  assert.equal(view.selection("ps-scope"), "ai");
  assert.equal(view.selection("ps-ai-target"), "worker-a");
  assert.equal(view.text(), "未保存 <保留> & 样例");
  assert.equal(view.selection("ps-prompt-role"), "report");
  view.click("loaded");
  view.click("apply-prompt");
  assert.ok(
    view.loadedTexts().includes(original),
    "Applying saved settings must exclude the unsaved draft",
  );
  assert.ok(!view.loadedTexts().includes("未保存 <保留> & 样例"));
  view.cleanup();
});

test("saving changes future configuration; only active application changes the selected AI snapshot", () => {
  const app = runtime();
  const view = prompts(app.open());
  view.click("loaded");
  const initial = view.loadedTexts()[0];
  saveText(view, "新的全局原则");
  assert.equal(view.loadedTexts()[0], initial);
  view.click("apply-prompt");
  assert.equal(view.loadedTexts()[0], "新的全局原则");
  singleAI(view, "worker-a");
  view.click("loaded");
  assert.equal(
    view.loadedTexts()[0],
    initial,
    "Another AI retains its startup snapshot",
  );
  view.click("apply-prompt");
  assert.equal(view.loadedTexts()[0], "新的全局原则");
  view.cleanup();

  const next = prompts(app.open("workspace-a", "new-session-task"));
  next.click("loaded");
  assert.equal(
    next.loadedTexts()[0],
    "新的全局原则",
    "A newly registered sample session uses saved configuration",
  );
  next.cleanup();
});

test("appearance survives a fresh runtime and topbar theme changes use the same saved preference", () => {
  const app = runtime();
  const view = app.open();
  view.click("section", "appearance");
  view.change("ps-theme", "dark");
  view.change("ps-motion", "reduced");
  view.change("ps-font", "large");
  assert.deepEqual(app.document.documentElement.dataset, {
    theme: "dark",
    motion: "reduced",
    fontSize: "large",
  });
  assert.match(view.html(), /prototype-settings ps-embedded/);
  view.cleanup();

  const refreshed = runtime(app.storage);
  refreshed.settings.applyAppearance();
  assert.deepEqual(refreshed.document.documentElement.dataset, {
    theme: "dark",
    motion: "reduced",
    fontSize: "large",
  });
  assert.equal(refreshed.settings.toggleTheme(), "light");
  const final = runtime(app.storage);
  final.settings.applyAppearance();
  assert.deepEqual(final.document.documentElement.dataset, {
    theme: "light",
    motion: "reduced",
    fontSize: "large",
  });
});
