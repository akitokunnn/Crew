/* 手机外壳：底部标签栏和“更多”面板。只在窄屏生效，不碰业务状态：
   导航按钮沿用页面已有的 data-act（由 app.js 的委托点击处理），角标从桌面图标栏读取。 */
(() => {
  "use strict";
  const narrow = window.matchMedia("(max-width:760px)");
  const body = document.body;
  const page = body.classList.contains("assistant-page") ? "assistant" : body.classList.contains("notes-page") ? "notes" : "app";
  const STORE = "crew.mobile.project", OPEN_LEADERS = "crew.mobile.openLeaders";
  const query = new URLSearchParams(location.search).get("project");
  // 助手、想法页带 ?project= 时属于该工作区；不带的助手页是系统管理员，底栏仍指向最近的工作区。
  const scope = window.CrewBrowser?.pageProject ? window.CrewBrowser.pageProject() : {mode: query ? "workspace" : "home", project: query};
  const pageProject = page !== "app" && scope.mode === "workspace" ? scope.project : null;
  let project = page === "app" ? query : pageProject;
  try {
    if (project) localStorage.setItem(STORE, project);
    else if (page !== "app") project = localStorage.getItem(STORE);
  } catch { /* 无痕模式下不记最近工作区，链接回到工作区入口 */ }
  const adminPage = page === "assistant" && pageProject === null;
  const pageUrl = kind => project ? `/${kind}.html?project=${encodeURIComponent(project)}` : "/";
  const inProject = page === "app" && query !== null;
  const projectUrl = hash => project ? `/?project=${encodeURIComponent(project)}${hash ? "#" + hash : ""}` : "/";

  const icon = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
    leaders: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="16.8" cy="9.5" r="2.6"/><path d="M16.2 14.1c2.4.1 3.9 1.7 4.3 4.4"/>',
    notes: '<path d="M14.5 4.5 19.5 9.5 9 20H4v-5z"/><path d="m12.5 6.5 5 5"/>',
    assistant: '<path d="M5 5h14a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1h-7l-4.5 3.5v-3.5H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/><path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01"/>',
    more: '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
    channel: '<path d="M4 6h16v10H9l-5 4z"/>',
    room: '<rect x="4" y="5" width="16" height="14" rx="2.5"/><path d="M4 9h16m-11 4h6m-6 3h4"/>',
    architecture: '<rect x="9" y="3" width="6" height="5" rx="1.5"/><rect x="3" y="16" width="6" height="5" rx="1.5"/><rect x="15" y="16" width="6" height="5" rx="1.5"/><path d="M12 8v4M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>',
    git: '<circle cx="6.5" cy="5.5" r="2"/><circle cx="6.5" cy="18.5" r="2"/><circle cx="17.5" cy="8" r="2"/><path d="M6.5 7.5v9M17.5 10c0 4-5 3.5-9.5 7"/>',
    usage: '<path d="M5 19V11M10 19V5M15 19v-6M20 19V9"/>',
    todos: '<path d="m5 12 3.5 3.5L19 6"/><path d="M5 19h14"/>',
    switch: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    theme: '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5A6.5 6.5 0 0 1 12 3.5z"/>',
    presence: '<circle cx="12" cy="7" r="3"/><path d="M6 20c.7-4 3-6 6-6s5.3 2 6 6"/>',
    admin: '<path d="M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6l-8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/>',
  };

  // 在项目页里用页面已有的 data-act 导航；其他页面用链接回到最近的工作区。
  const navAttrs = view => inProject ? `type="button" data-act="view" data-view="${view}"` : `href="${projectUrl(view)}"`;
  const tag = view => inProject ? "button" : "a";
  const tab = (key, label, attrs, el = "a") => `<${el} class="m-tab" data-m-tab="${key}" aria-label="${label}" ${attrs}>${icon(ICONS[key])}<span>${label}</span></${el}>`;

  function buildTabbar() {
    const bar = document.createElement("nav");
    bar.id = "m-tabbar"; bar.setAttribute("aria-label", "主导航");
    const leadersAttrs = inProject ? 'type="button" data-act="leaders-toggle"' : `href="${projectUrl("all")}" data-m-open-leaders`;
    bar.innerHTML = [
      tab("home", "工作台", navAttrs("all"), tag()),
      tab("leaders", "Leader", leadersAttrs, inProject ? "button" : "a"),
      // 想法和助手都属于工作区；还没有工作区时先回到工作区入口。
      tab("notes", "想法", `href="${pageUrl("notes")}"`),
      tab("assistant", "助手", `href="${pageUrl("assistant")}"`),
      tab("more", "更多", 'type="button" data-m-act="more" aria-haspopup="dialog" aria-controls="m-sheet"', "button"),
    ].join("");
    return bar;
  }

  function sheetItem(key, label, attrs, el, extra = "") {
    return `<${el} class="m-sheet-item" ${attrs}>${icon(ICONS[key])}<span>${label}</span>${extra}</${el}>`;
  }
  function buildSheet() {
    const sheet = document.createElement("section");
    sheet.id = "m-sheet"; sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-label", "更多"); sheet.setAttribute("aria-modal", "true");
    sheet.dataset.open = "false"; sheet.inert = true;
    const go = [
      sheetItem("channel", "Leader 交流区", navAttrs("channel"), tag(), '<em class="m-count" data-m-count="channel" hidden></em>'),
      sheetItem("room", "作战室", inProject ? 'type="button" data-act="room-list"' : `href="${projectUrl("war-room=0")}"`, tag(), '<small data-m-room-state></small>'),
      sheetItem("todos", "待办", navAttrs("todos"), tag()),
      sheetItem("architecture", "架构图", navAttrs("architecture"), tag()),
      sheetItem("git", "Git 助手", navAttrs("git"), tag(), '<em class="m-count" data-m-count="git" hidden></em>'),
      sheetItem("usage", "用量与额度", navAttrs("usage"), tag()),
      sheetItem("switch", "切换工作区", 'href="/" data-m-always', "a"),
      ...(window.crewDesktopShell || window.crewDesktopTheme ? [] : [sheetItem("admin", "系统管理员", 'href="/assistant.html" data-m-always', "a")]),
    ];
    const prefs = inProject ? [
      sheetItem("theme", "日间 / 夜间", 'type="button" data-act="theme"', "button", '<small data-m-theme></small>'),
      sheetItem("presence", "在场与消息提醒", 'type="button" data-act="presence-toggle"', "button"),
      sheetItem("settings", "设置", 'type="button" data-act="settings" data-local-control', "button"),
    ] : [];
    const always = go.filter(item => /data-m-always/.test(item));
    sheet.innerHTML = `<p class="m-sheet-title">${project ? "当前工作区" : "工作区"}</p><div class="m-sheet-group">${project ? go.join("") : always.join("")}</div>${prefs.length ? `<p class="m-sheet-title">偏好</p><div class="m-sheet-group">${prefs.join("")}</div>` : ""}`;
    const backdrop = document.createElement("div");
    backdrop.id = "m-sheet-backdrop"; backdrop.dataset.open = "false";
    return [backdrop, sheet];
  }

  let tabbar, sheet, backdrop, lastFocus = null;
  function setSheet(open) {
    sheet.dataset.open = backdrop.dataset.open = String(open); sheet.inert = !open;
    tabbar.querySelector('[data-m-tab="more"]').setAttribute("aria-expanded", String(open));
    if (open) { lastFocus = document.activeElement; sheet.querySelector(".m-sheet-item")?.focus({preventScroll: true}); }
    else lastFocus?.focus?.({preventScroll: true});
  }

  function currentView() {
    if (page !== "app") return page;
    const hash = location.hash.slice(1), params = new URLSearchParams(hash);
    if (params.has("leader") || params.has("history")) return "chat";
    return hash || "all";
  }
  function sync() {
    const leaders = document.getElementById("leader-panel");
    const leadersOpen = inProject && leaders?.dataset.open === "true";
    const view = currentView();
    body.classList.toggle("m-chat", view === "chat");
    body.classList.toggle("m-leaders", leadersOpen);
    const active = leadersOpen ? "leaders" : page === "app" ? (["all", "todos"].includes(view) || !inProject ? "home" : view === "chat" ? "leaders" : "more") : adminPage ? "more" : page;
    for (const item of tabbar.querySelectorAll(".m-tab")) {
      if (item.dataset.mTab === active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    }
    // 角标取自桌面图标栏，保持同一份计数
    const rail = document.getElementById("sidebar");
    const homeCount = rail?.querySelector(".rail-home .rail-badge")?.textContent.trim() || "";
    const gitCount = rail?.querySelector(".rail-git .rail-badge")?.textContent.trim() || "";
    const unread = Boolean(rail?.querySelector(".leader-entry .rail-dot"));
    const room = rail?.querySelector(".rail-war-room");
    const waitingForRoom = room?.dataset.warRoomState === "waiting";
    const channelUnread = leaders?.querySelector(".leader-panel-channel .b-unread")?.textContent.trim() || "";
    badge(tabbar.querySelector('[data-m-tab="home"]'), homeCount);
    dot(tabbar.querySelector('[data-m-tab="leaders"]'), unread);
    const more = tabbar.querySelector('[data-m-tab="more"]');
    dot(more, Boolean(gitCount || channelUnread || waitingForRoom));
    if (waitingForRoom !== more.hasAttribute("data-room-waiting")) more.toggleAttribute("data-room-waiting", waitingForRoom);
    const roomState = sheet.querySelector("[data-m-room-state]");
    const roomLabel = room?.getAttribute("aria-label").split(" · ")[1] ?? "";
    if (roomState && roomState.textContent !== roomLabel) roomState.textContent = roomLabel;
    count("git", gitCount); count("channel", channelUnread);
    const theme = rail?.querySelector('[data-act="theme"]')?.getAttribute("title")?.split("·")[1]?.trim();
    const themeLabel = sheet.querySelector("[data-m-theme]"); if (themeLabel && theme && themeLabel.textContent !== theme) themeLabel.textContent = theme;
  }
  function badge(host, value) {
    let node = host.querySelector(".m-badge");
    if (!value) { node?.remove(); return; }
    if (!node) { node = document.createElement("i"); node.className = "m-badge"; host.append(node); }
    if (node.textContent !== value) node.textContent = value; host.setAttribute("aria-label", `${host.textContent.replace(value, "").trim()}，${value} 件待处理`);
  }
  function dot(host, on) {
    const node = host.querySelector(".m-dot");
    if (on && !node) { const item = document.createElement("i"); item.className = "m-dot"; item.setAttribute("aria-hidden", "true"); host.append(item); }
    if (!on) node?.remove();
  }
  function count(key, value) {
    const node = sheet.querySelector(`[data-m-count="${key}"]`); if (!node) return;
    node.hidden = !value; if (node.textContent !== value) node.textContent = value;
  }

  let scheduled = false;
  const schedule = () => { if (scheduled) return; scheduled = true; queueMicrotask(() => { scheduled = false; sync(); }); };

  function mount() {
    tabbar = buildTabbar(); [backdrop, sheet] = buildSheet();
    body.append(tabbar, backdrop, sheet);
    tabbar.addEventListener("click", event => {
      if (event.target.closest('[data-m-act="more"]')) { setSheet(sheet.dataset.open !== "true"); return; }
      const link = event.target.closest("[data-m-open-leaders]");
      if (link) { try { sessionStorage.setItem(OPEN_LEADERS, "1"); } catch { /* 只是少一次自动展开 */ } }
      if (event.target.closest('[data-m-tab="leaders"]') && document.getElementById("leader-panel")?.dataset.open === "true") {
        event.stopPropagation(); // 已在 Leader 列表时不收起
      }
    }, true);
    // 面板里的项沿用 data-act，点完先收起面板，再交给页面的委托处理
    sheet.addEventListener("click", event => {
      const item = event.target.closest(".m-sheet-item"); if (!item) return;
      setSheet(false);
      // 从 Leader 列表上打开的面板：跳转前先收起列表
      if ((item.dataset.view || item.dataset.act === "room-list" || item.tagName === "A") && document.getElementById("leader-panel")?.dataset.open === "true") document.getElementById("leader-entry")?.click();
    });
    backdrop.addEventListener("click", () => setSheet(false));
    document.addEventListener("keydown", event => { if (event.key === "Escape" && sheet.dataset.open === "true") setSheet(false); });
    for (const method of ["pushState", "replaceState"]) {
      const original = history[method];
      history[method] = function (...args) { const result = original.apply(this, args); schedule(); return result; };
    }
    window.addEventListener("hashchange", schedule);
    new MutationObserver(schedule).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ["data-open", "class"]});
    if (inProject) {
      let wanted = false;
      try { wanted = sessionStorage.getItem(OPEN_LEADERS) === "1"; sessionStorage.removeItem(OPEN_LEADERS); } catch { wanted = false; }
      if (wanted) {
        const open = () => { const entry = document.getElementById("leader-entry"); if (!entry) return false; if (document.getElementById("leader-panel").dataset.open !== "true") entry.click(); return true; };
        if (!open()) { const watch = new MutationObserver(() => { if (open()) watch.disconnect(); }); watch.observe(document.getElementById("sidebar"), {childList: true}); }
      }
    }
    sync();
  }

  function apply() {
    if (narrow.matches && !tabbar) mount();
    if (tabbar) { tabbar.hidden = !narrow.matches; if (!narrow.matches && sheet.dataset.open === "true") setSheet(false); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
  narrow.addEventListener("change", apply);
})();
