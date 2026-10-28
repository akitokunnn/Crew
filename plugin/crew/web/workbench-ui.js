"use strict";
(() => {
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const states = {
    discussion: "讨论中",
    queued: "等待执行",
    running: "推进中",
    waiting_user: "等你处理",
    stopped: "已停止",
    blocked: "受阻",
    completed: "已完成",
    archived: "已归档",
    trashed: "垃圾箱",
  };
  const icons = {
    home: '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
    chat: '<path d="M4 4h16v12H9l-5 4Z"/>',
    list: '<path d="M9 6h12M9 12h12M9 18h12M3 6h1M3 12h1M3 18h1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    settings:
      '<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
    pin: '<path d="m9 3 9 9-3 1-2 4-6-6 4-2Z"/><path d="m8 16-5 5"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    folder: '<path d="M3 6h7l2 3h9v11H3Z"/>',
  };
  const icon = (name) =>
    `<svg class="wb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.arrow}</svg>`;
  const requestId = () => crypto.randomUUID();
  function legacyURL(project, view = "all", settings) {
    const query = new URLSearchParams({ legacy: "1" });
    if (project) query.set("project", project);
    const mode = new URLSearchParams(location.search).get("data");
    if (mode) query.set("data", mode);
    if (settings) query.set("settings", settings);
    return `/?${query}#${view}`;
  }
  const memory = new Map();
  const storagePrefix = `crew.workbench.${window.CrewClient?.current?.mode || "real"}.`;
  function remember(key, value) {
    memory.set(storagePrefix + key, value);
    try {
      sessionStorage.setItem(storagePrefix + key, JSON.stringify(value));
    } catch (_) {
      /* memory remains usable */
    }
  }
  function recall(key, fallback) {
    if (memory.has(storagePrefix + key)) return memory.get(storagePrefix + key);
    try {
      const raw = sessionStorage.getItem(storagePrefix + key);
      if (raw !== null) return JSON.parse(raw);
    } catch (_) {
      /* storage may be disabled */
    }
    return fallback;
  }
  function status(task) {
    const label = task.lifecycle_pending
      ? "正在停止…"
      : task.execution_status === "pausing"
        ? "正在停止…"
        : states[task.state] || task.state || "未知";
    return `<span class="wb-status" data-state="${escape(task.state)}"><i></i>${escape(label)}</span>`;
  }
  window.CrewWorkbenchUI = {
    escape,
    icon,
    requestId,
    legacyURL,
    remember,
    recall,
    status,
  };
})();
