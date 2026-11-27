"use strict";
(() => {
  const STORE_PROJECT = "crew.mobile.project";
  const STORE_ROUTE = "crew.lastProjectRoute";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const projectDisplay = item => item.display_name || item.name;
  const projectHref = (name, hash = "") => name ? `/?project=${encodeURIComponent(name)}${hash ? "#" + hash : ""}` : "/";
  const icons = {
    team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-.5a6.5 6.5 0 0 1 13 0v.5M15.5 4.7a3.5 3.5 0 0 1 0 6.6M18 13.6a6.5 6.5 0 0 1 3.5 5.9v.5"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    board: '<rect x="9" y="3" width="6" height="5" rx="1.5"/><rect x="3" y="16" width="6" height="5" rx="1.5"/><rect x="15" y="16" width="6" height="5" rx="1.5"/><path d="M12 8v4M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>',
    pencil: '<path d="m16 3 5 5L8 21H3v-5L16 3Zm-3 3 5 5"/>',
    msg: '<path d="M21 15a3 3 0 0 1-3 3H9l-6 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v9Z"/>',
    shield: '<path d="M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6l-8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    branch: '<circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12m0-5h5a7 7 0 0 0 7-5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon: '<path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/>',
    auto: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M12 4v13"/>',
    settings: '<path d="M9.5 3h5l.5 2.2 1.5.9 2.2-.7 2.5 4.3-1.7 1.5V13l1.7 1.5-2.5 4.3-2.2-.7-1.5.9-.5 2.2h-5L9 19l-1.5-.9-2.2.7-2.5-4.3L4.5 13v-1.8L2.8 9.7l2.5-4.3 2.2.7L9 5.2 9.5 3Z"/><circle cx="12" cy="12" r="3"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    right: '<path d="m9 5 7 7-7 7"/>'
  };
  const ic = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${icons[name]}</svg>`;
  const roomStates = {empty:"没房", preparing:"筹备中", active:"作战中", waiting:"等你定", loading:"读取中", error:"读取失败"};
  function roomState(rooms) {
    const open = rooms.filter(room => room.status === "open");
    if (open.some(room => room.open_decisions > 0 || room.phase === "preparing" && room.draft_version > 0 || room.budget?.paused_at)) return "waiting";
    if (open.some(room => room.phase === "active")) return "active";
    return open.some(room => room.phase === "preparing" && room.draft_version === 0) ? "preparing" : "empty";
  }
  const roomLabel = rooms => roomStates[roomState(rooms)];
  function roomMarkup(project, rooms, selected = false, failed = false) {
    const state = failed ? "error" : rooms === null ? "loading" : roomState(rooms);
    const label = `作战室 · ${roomStates[state]}`;
    const mark = {empty:'<path d="M9 15h6" stroke-dasharray="1 2"/>', preparing:'<path d="M9 13h6m-6 3h4"/>',
      active:'<path d="m9 16 3-4 2 2 2-3"/>', waiting:'<path d="M10 13h4m-4 3h4"/>'}[state] ?? "";
    return `<a class="rail-button rail-war-room${selected ? " active" : ""}" data-war-room-state="${state}" data-preserve="war-room-entry" href="${esc(projectHref(project, "war-room=0"))}" title="${label}" aria-label="${label}"${selected ? ' aria-current="page"' : ""}><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2.5"/><path d="M4 9h16"/>${mark}</svg>${state === "waiting" ? '<span class="rail-dot" aria-hidden="true"></span>' : ""}</a>`;
  }

  const FALLBACK_COLORS = ["#3E7C74", "#8A5A7E", "#4F6B8F", "#5E7F55", "#A07A3A", "#A8604F"];
  function fallbackAccent(name) {
    let n = 0;
    for (const ch of String(name || "")) n = (n + ch.charCodeAt(0)) % FALLBACK_COLORS.length;
    return FALLBACK_COLORS[n];
  }
  function logoMarkup() {
    if (window.CrewTheme?.logo) return window.CrewTheme.logo();
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6.5" fill="currentColor"/><path d="M14.26 8.54 A4.90 4.90 0 1 0 14.26 15.46" fill="none" stroke="var(--on-accent)" stroke-width="3" stroke-linecap="round"/><circle cx="17.4" cy="16.4" r="1.75" fill="var(--on-accent)"/></svg>';
  }
  function itemStyle(themes, item) {
    if (Array.isArray(themes) && themes.length && window.CrewTheme?.get && window.CrewTheme?.projectStyle && CrewTheme.get(themes, item.theme)) {
      return CrewTheme.projectStyle(themes, item.theme);
    }
    const color = fallbackAccent(item.name);
    return `--project-accent-light:${color};--project-accent-dark:${color};--project-text-light:${color};--project-text-dark:${color}`;
  }

  function popoverMarkup({projects = [], themes = [], currentProject = null, recentProject = null, inProject = false, localControls = true, open = false} = {}) {
    const list = Array.isArray(projects) ? projects : [];
    const items = list.length ? list.map(item => {
      const active = currentProject !== null && item.name === currentProject;
      const recent = !active && recentProject !== null && item.name === recentProject;
      const meta = active ? "当前工作区" : recent ? "最近访问" : (item.root || "");
      return `<a class="ws-item ${active ? "active" : ""}" style="${esc(itemStyle(themes, item))}" href="${esc(projectHref(item.name, "all"))}" ${active ? 'aria-current="true"' : ""}>
        <span class="ws-logo" aria-hidden="true">${logoMarkup()}</span>
        <span class="ws-meta"><strong>${esc(projectDisplay(item))}</strong>${meta ? `<small title="${esc(meta)}">${esc(meta)}</small>` : ""}</span>
        ${active ? `<span class="ws-check" aria-hidden="true">${ic("check", "sm")}</span>` : `<span class="ws-go" aria-hidden="true">${ic("right", "xs")}</span>`}
      </a>`;
    }).join("") : '<p class="ws-empty">暂无工作区</p>';
    const wsSettings = inProject && localControls
      ? `<button type="button" class="ws-foot-btn" data-act="workspace-settings">${ic("settings", "sm")}<span>当前工作区设置…</span></button>`
      : "";
    return `<section id="workspace-popover" class="ws-popover" aria-label="切换工作区" ${open ? "" : "hidden"}>
      <header class="ws-head"><span>切换工作区</span><span class="ws-count">${list.length}</span></header>
      <div class="ws-list">${items}</div>
      <footer class="ws-foot">${wsSettings}<a class="ws-foot-btn" href="/">${ic("grid", "sm")}<span>工作区总览与新建</span></a></footer>
    </section>`;
  }

  function readRecentProject() {
    try { return localStorage.getItem(STORE_PROJECT) || null; } catch { return null; }
  }
  function readLastRoute(project) {
    if (!project) return "all";
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_ROUTE) || "null");
      if (raw && raw.project === project && typeof raw.hash === "string" && raw.hash) return raw.hash;
    } catch {}
    return "all";
  }

  const page = document.body.classList.contains("assistant-page") ? "assistant"
    : document.body.classList.contains("notes-page") ? "notes" : "app";
  // A page with ?project=X belongs to that workspace (its assistant or ideas); without it, the assistant page is 系统管理员.
  const pageScope = window.CrewBrowser?.pageProject ? window.CrewBrowser.pageProject() : {mode: "home", project: null};
  const pageProject = pageScope.mode === "workspace" ? pageScope.project : null;
  const pageHref = (kind, name) => `/${kind}.html?project=${encodeURIComponent(name)}`;
  // Inside the Crew desktop the 系统管理员 entry is the window's top-right button, not a rail item.
  const desktop = Boolean(window.crewDesktopShell || window.crewDesktopTheme);

  let projects = [];
  let popoverOpen = false;
  let themeMode = "system";

  function readThemeMode() {
    if (!window.crewDesktopTheme) {
      try {
        const saved = localStorage.getItem("crew.theme");
        if (["system", "light", "dark"].includes(saved)) return saved;
      } catch {}
    }
    return "system";
  }
  function applyThemeMode(mode) {
    themeMode = mode;
    if (window.CrewTheme?.setMode) CrewTheme.setMode(mode);
    else if (mode === "system") document.documentElement.dataset.theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    else document.documentElement.dataset.theme = mode;
  }
  async function cycleTheme() {
    const next = {system: "light", light: "dark", dark: "system"}[themeMode] || "system";
    if (window.crewDesktopTheme?.set) {
      try { await window.crewDesktopTheme.set(next); } catch {}
    } else {
      try { localStorage.setItem("crew.theme", next); } catch {}
    }
    applyThemeMode(next);
    window.dispatchEvent(new Event("crew:theme-change"));
    renderStandalone();
  }

  function setPopoverOpen(open) {
    popoverOpen = open;
    const pop = document.getElementById("workspace-popover");
    const entry = document.getElementById("workspace-entry");
    if (pop) pop.hidden = !open;
    if (entry) {
      entry.classList.toggle("active", open);
      entry.setAttribute("aria-expanded", String(open));
    }
  }

  function targetWorkspace() {
    if (pageProject) return pageProject;
    const recent = readRecentProject();
    if (recent && (!projects.length || projects.some(item => item.name === recent))) return recent;
    return projects[0]?.name || recent || null;
  }

  function renderStandalone() {
    if (page === "app") return;
    const host = document.getElementById("sidebar");
    if (!host) return;
    const recent = readRecentProject();
    const target = targetWorkspace();
    const targetItem = projects.find(item => item.name === target);
    const targetLabel = targetItem ? projectDisplay(targetItem) : (target || "工作区");
    const leaderHash = readLastRoute(target);
    const themeIcon = {system: "auto", light: "sun", dark: "moon"}[themeMode] || "auto";
    const themeLabel = {system: "跟随系统", light: "日间", dark: "夜间"}[themeMode] || "跟随系统";
    const localControls = !window.CrewAccess || CrewAccess.local_controls;
    const settingsHref = target ? projectHref(target, "settings") : "/#settings";
    const adminPage = page === "assistant" && pageProject === null;
    // Highlighted by the stable project name in the URL, so a workspace page never lights up another workspace's item.
    const railLink = (kind, name, display, title, icon) => {
      const active = page === kind && pageProject === name;
      return `<a class="rail-button ${active ? "active" : ""}" href="${esc(pageHref(kind, name))}" ${active ? 'aria-current="page"' : ""} title="${esc(display)} · ${title}" aria-label="${esc(display)} · ${title}">${ic(icon)}</a>`;
    };

    host.innerHTML = [
      `<button type="button" id="workspace-entry" class="rail-brand ${popoverOpen ? "active" : ""}" data-rail-act="workspace-switcher" title="${esc(targetLabel)} · 切换工作区" aria-label="${esc(targetLabel)} · 切换工作区" aria-controls="workspace-popover" aria-expanded="${popoverOpen}">${logoMarkup()}<span class="rail-brand-caret" aria-hidden="true"></span></button>`,
      `<a class="rail-button leader-entry" href="${esc(projectHref(target, leaderHash))}" title="回到 ${esc(targetLabel)} · Leader" aria-label="回到 ${esc(targetLabel)} · Leader">${ic("team")}</a>`,
      target ? roomMarkup(target, roomProject === target ? standaloneRooms : null, false, roomProject === target && roomFailed) : "",
      '<div class="rail-divider"></div>',
      `<span class="rail-home"><a class="rail-button" href="${esc(projectHref(target, "all"))}" title="回到 ${esc(targetLabel)} · 主页" aria-label="回到 ${esc(targetLabel)} · 主页">${ic("grid")}</a></span>`,
      `<a class="rail-button" href="${esc(projectHref(target, "architecture"))}" title="${esc(targetLabel)} · 架构图" aria-label="${esc(targetLabel)} · 架构图">${ic("board")}</a>`,
      ...(target ? [railLink("assistant", target, targetLabel, "助手", "msg"), railLink("notes", target, targetLabel, "想法", "pencil")] : []),
      `<span class="rail-git"><a class="rail-button" href="${esc(projectHref(target, "git"))}" title="${esc(targetLabel)} · Git" aria-label="${esc(targetLabel)} · Git">${ic("branch")}</a></span>`,
      '<span class="rail-space"></span>',
      desktop ? "" : `<a class="rail-button rail-admin ${adminPage ? "active" : ""}" href="/assistant.html" ${adminPage ? 'aria-current="page"' : ""} title="系统管理员" aria-label="系统管理员">${ic("shield")}</a>`,
      `<button type="button" class="rail-button" data-rail-act="theme" title="日间 / 夜间 · ${esc(themeLabel)}" aria-label="日间 / 夜间 · ${esc(themeLabel)}">${ic(themeIcon)}</button>`,
      `<a class="rail-button" data-local-control href="${esc(settingsHref)}" title="设置" aria-label="设置">${ic("settings")}</a>`,
      popoverMarkup({projects, themes: [], currentProject: pageProject, recentProject: recent || target, inProject: false, localControls, open: popoverOpen})
    ].join("");
    if (target && roomProject !== target) void loadStandaloneRooms(target);
  }

  let roomProject = null, standaloneRooms = null, roomFailed = false, roomRequest = 0;
  async function loadStandaloneRooms(target) {
    if (!target) return;
    const request = ++roomRequest;
    if (roomProject !== target) { standaloneRooms = null; roomFailed = false; }
    roomProject = target;
    try {
      if (window.CrewAccess && !await CrewAccess.ready) return;
      const path = `/api/op/war_room.list?${new URLSearchParams({project:target})}`;
      const res = await fetch(path, {headers:{"X-Crew":"1"}});
      const data = await res.json();
      if (!res.ok) throw Error(data.error);
      if (request !== roomRequest) return;
      standaloneRooms = data.rooms;
      roomFailed = false;
    } catch (error) {
      if (request !== roomRequest) return;
      roomFailed = true;
    }
    renderStandalone();
  }

  function update(data = {}) {
    if (Array.isArray(data.projects)) {
      projects = data.projects;
      if (page !== "app") renderStandalone();
    }
  }

  if (page !== "app") {
    // Inside the Crew desktop the font size is desktop-wide and arrives with the display mode.
    const desktopFont = value => { if (typeof value?.fontScale === "number") document.documentElement.style.setProperty("--fs", String(value.fontScale)); };
    if (!window.crewDesktopTheme) try {
      const fs = Number(localStorage.getItem("crew.fontScale"));
      if (fs >= 0.9 && fs <= 1.4) document.documentElement.style.setProperty("--fs", String(fs));
    } catch {}
    themeMode = readThemeMode();
    if (window.crewDesktopTheme) {
      window.crewDesktopTheme.onChange?.(value => {
        desktopFont(value);
        if (["system", "light", "dark"].includes(value?.mode)) {
          themeMode = value.mode;
          renderStandalone();
        }
      });
      void window.crewDesktopTheme.get?.().then(value => {
        desktopFont(value);
        if (["system", "light", "dark"].includes(value?.mode)) {
          themeMode = value.mode;
          renderStandalone();
        }
      }).catch(() => {});
    }
    window.addEventListener("storage", () => {
      themeMode = readThemeMode();
      renderStandalone();
    });
    window.addEventListener("focus", () => void loadStandaloneRooms(targetWorkspace()));
    document.addEventListener("pointerdown", event => {
      if (popoverOpen && !event.target.closest("#workspace-popover,#workspace-entry")) setPopoverOpen(false);
    });
    document.addEventListener("click", event => {
      const btn = event.target.closest("[data-rail-act]");
      if (!btn) return;
      if (btn.dataset.railAct === "workspace-switcher") setPopoverOpen(!popoverOpen);
      else if (btn.dataset.railAct === "theme") void cycleTheme();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && popoverOpen) {
        event.preventDefault();
        const inside = document.activeElement?.closest("#workspace-popover");
        setPopoverOpen(false);
        if (inside) document.getElementById("workspace-entry")?.focus({preventScroll: true});
      }
    });
    renderStandalone();
    (async () => {
      if (window.CrewAccess && !await CrewAccess.ready) return;
      if (projects.length) return;
      try {
        const res = await fetch("/api/op/workspace.list", {headers: {"X-Crew": "1"}});
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.projects) && !projects.length) update({projects: data.projects});
      } catch {}
    })();
  }

  window.CrewGlobalRail = {popoverMarkup, update, projectHref, roomMarkup, roomState, roomLabel, STORE_PROJECT, STORE_ROUTE};
})();
