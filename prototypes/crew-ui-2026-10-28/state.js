(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CrewPrototypeState = api;
})(globalThis, function () {
  "use strict";
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const keyFor = (task, person = "leader") => `${task}:${person}`;
  function create(sample, saved) {
    const state =
      saved && saved.schema === 1
        ? saved
        : {
            schema: 1,
            workspaces: clone(sample.workspaces || []),
            tasks: clone(sample.tasks),
            todos: clone(sample.todos),
            conversations: {},
            drafts: {},
            positions: {},
            panels: {},
            choices: {},
            messages: {},
            queues: {},
            stopped: {},
            notifications: [],
            settingsReturn: null,
          };
    function task(id) {
      const result = state.tasks.find((t) => t.id === id);
      if (!result) throw new Error("找不到样例任务");
      return result;
    }
    function draft(id, person, value) {
      const key = keyFor(id, person);
      if (value !== undefined) state.drafts[key] = value;
      return state.drafts[key] || "";
    }
    function send(id, person, text, queued = false) {
      if (!text.trim()) return false;
      const key = keyFor(id, person);
      const items = queued ? state.queues : state.messages;
      (items[key] ||= []).push({
        text,
        role: "user",
        id: Date.now() + "-" + Math.random().toString(16).slice(2),
      });
      state.drafts[key] = "";
      if (
        !queued &&
        state.tasks.find((t) => t.id === id)?.state !== "stopped"
      ) {
        state.stopped[key] = false;
        state.conversations[key] = "working";
        const member = state.tasks
          .find((t) => t.id === id)
          ?.members.find((m) => m.id === person);
        if (member) member.state = "working";
      }
      return true;
    }
    function stopConversation(id, person) {
      state.stopped[keyFor(id, person)] = true;
      state.conversations[keyFor(id, person)] = "stopped";
      const member = state.tasks
        .find((t) => t.id === id)
        ?.members.find((m) => m.id === person);
      if (member) member.state = "stopped";
    }
    function stopTask(id) {
      const t = task(id);
      t.beforeStop = {
        task: t.state,
        members: Object.fromEntries(t.members.map((m) => [m.id, m.state])),
        conversations: {},
        stopped: {},
      };
      ["leader", ...t.members.map((m) => m.id)].forEach((p) => {
        const key = keyFor(id, p);
        t.beforeStop.conversations[key] = state.conversations[key] || "working";
        t.beforeStop.stopped[key] = state.stopped[key] || false;
      });
      t.state = "stopped";
      ["leader", ...t.members.map((m) => m.id)].forEach((p) =>
        stopConversation(id, p),
      );
      t.members.forEach((m) => {
        m.state = "stopped";
      });
    }
    function resumeTask(id) {
      const t = task(id),
        before = t.beforeStop;
      t.state = before?.task || "working";
      ["leader", ...t.members.map((m) => m.id)].forEach((p) => {
        const key = keyFor(id, p);
        state.stopped[key] = before?.stopped[key] || false;
        state.conversations[key] = before?.conversations[key] || "working";
      });
      t.members.forEach((m) => {
        m.state = before?.members[m.id] || "working";
      });
    }
    function newTask(text, workspace) {
      const id = "sample-" + Date.now();
      state.tasks.unshift({
        id,
        title: text.trim().slice(0, 22),
        workspace,
        related: [],
        leader: "林序",
        state: "working",
        update: "从这次对话开始，正在整理任务核心",
        version: null,
        fresh: false,
        auto: false,
        pinned: false,
        goal: text,
        criteria: "随对话逐步明确，可在任务详情中修改。",
        members: [],
      });
      send(id, "leader", text);
      state.messages[keyFor(id, "leader")].push({
        role: "ai",
        text: "已经从这段对话整理出任务核心。我们可以继续明确条件，也可以直接推进；任务详情随时可改。（预设样例回复）",
      });
      return id;
    }
    function addTodo(text, workspace) {
      if (!text.trim()) return;
      const item = {
        id: "todo-" + Date.now(),
        text,
        workspace,
        tags: ["AI 标签 · 待整理"],
        done: false,
        task: null,
        note: "原话已保存。",
      };
      state.todos.unshift(item);
      state.notifications.unshift({
        todo: item.id,
        text: "样例通知：QQ / Lark · " + text,
      });
      return item;
    }
    function completeTodo(id) {
      const t = state.todos.find((x) => x.id === id);
      t.done = !t.done;
      /* A manual stop must never be overridden by TODO completion. */ return t;
    }
    return {
      state,
      task,
      draft,
      send,
      stopConversation,
      stopTask,
      resumeTask,
      newTask,
      addTodo,
      completeTodo,
      keyFor,
    };
  }
  return { create, keyFor };
});
