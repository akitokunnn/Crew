"use strict";
// Workspace entry page ("/" without a project). Rendered by app.js on every state poll;
// the returned HTML must be a pure function of state and this module's cached activity.
(() => {
  const THROTTLE_MS = 10000;
  const INBOX_LIMIT = 6;
  // "Waiting for you" = what only the user can unblock; same set the summary counts.
  const ATTENTION = ["approval", "decision", "acceptance", "todo"];
  const KIND = {approval:"权限确认", decision:"等你拍板", acceptance:"待验收", todo:"待你动手", waiting:"等你回复",
    error:"出错", delivery:"已交付", working:"在干活", reply:"新回复"};
  // Card badges in reading order; zero counts are not shown.
  const BADGES = [["approvals", "approval"], ["decisions", "decision"], ["acceptance", "acceptance"], ["todos", "todo"], ["unread", "reply", "未读"]];
  const cache = {key:null, projects:null, error:null, at:0, inflight:false};
  let last = null;

  const waitingCount = counts => counts.decisions + counts.todos + counts.approvals + counts.acceptance;
  const projectHref = name => `/?project=${encodeURIComponent(name)}`;

  function refresh(names) {
    if (names.length === 0 || document.hidden || cache.inflight) return;
    const key = JSON.stringify(names);
    if (key === cache.key && Date.now() - cache.at < THROTTLE_MS) return;
    cache.inflight = true;
    const query = new URLSearchParams(names.map(name => ["project", name]));
    fetch(`/api/workspaces/activity?${query}`, {headers:{"X-Crew":"1"}})
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
      })
      .then(body => { cache.projects = new Map(body.projects.map(item => [item.project, item])); cache.error = null; },
        error => { cache.error = error.message; })
      .finally(() => { cache.key = key; cache.at = Date.now(); cache.inflight = false; repaint(); });
  }

  // Shows fresh activity right away instead of waiting for the next state poll. Mirrors app.js
  // replace(): same dataset.render bookkeeping, keyboard focus kept on the same entry.
  function repaint() {
    if (last === null || !document.getElementById("app").classList.contains("workspace-entry")) return;
    const main = document.getElementById("main");
    const html = view(last.state, last.helpers);
    if (main.dataset.render === html) return;
    const focused = main.contains(document.activeElement) ? document.activeElement.dataset.entry : undefined;
    main.innerHTML = html;
    main.dataset.render = html;
    if (focused !== undefined) Array.from(main.querySelectorAll("[data-entry]")).find(node => node.dataset.entry === focused)?.focus({preventScroll:true});
  }

  function ago(iso) {
    const minutes = Math.floor((Date.now() - Date.parse(iso)) / 60000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
    return `${Math.floor(minutes / 1440)} 天前`;
  }

  function summary(entries, loaded) {
    const parts = [`<span>${entries.length} 个工作区</span>`];
    if (loaded.length > 0) {
      const waiting = loaded.reduce((sum, item) => sum + waitingCount(item.counts), 0);
      const busy = loaded.filter(item => !item.unknown && item.counts.working > 0).length;
      const unknown = loaded.filter(item => item.unknown).length;
      parts.push(waiting > 0 ? `<strong class="entry-sum-waiting">${waiting} 件等你处理</strong>` : "<span>没有等你处理的事</span>");
      if (busy > 0) parts.push(`<span class="entry-sum-busy"><i class="entry-pulse" aria-hidden="true"></i>${busy} 个在干活</span>`);
      if (unknown > 0) parts.push(`<span>${unknown} 个运行状态未确认</span>`);
    }
    const failed = entries.filter(entry => entry.activity?.error !== undefined).length;
    if (failed > 0) parts.push(`<span class="entry-sum-failed">${failed} 个读取失败</span>`);
    return parts.join("");
  }

  function inbox(entries, loaded, themes, {esc, ic, display}) {
    const total = loaded.reduce((sum, item) => sum + waitingCount(item.counts), 0);
    const rows = entries.flatMap(({item, activity}) => activity === undefined || activity.error !== undefined ? []
      : activity.items.filter(task => ATTENTION.includes(task.kind)).map(task => ({item, task})))
      .sort((a, b) => ATTENTION.indexOf(a.task.kind) - ATTENTION.indexOf(b.task.kind))
      .slice(0, INBOX_LIMIT);
    let body;
    if (cache.projects === null && cache.error === null) body = `<div class="entry-inbox-loading" aria-label="读取中"><i></i><i></i></div>`;
    else if (loaded.length === 0) body = `<p class="entry-inbox-note">动态没有读到，暂时无法汇总</p>`;
    else if (total === 0) body = `<p class="entry-inbox-clear">${ic("check", "sm")}<span>没有在等你的事</span></p>`;
    else {
      const more = total - rows.length;
      body = `<ol class="entry-inbox-list">${rows.map(({item, task}, index) => `<li><a class="entry-task" data-entry="task:${index}" data-kind="${esc(task.kind)}" href="${esc(projectHref(item.name))}#all"><span class="entry-task-main"><span class="entry-task-meta"><span class="entry-kind" data-kind="${esc(task.kind)}">${KIND[task.kind]}</span><span class="entry-task-where"><i class="entry-swatch" style="${CrewTheme.projectStyle(themes, item.theme)}" aria-hidden="true"></i>${esc(display(item))}${task.name ? ` · ${esc(task.name)}` : ""}${task.at ? ` · ${ago(task.at)}` : ""}</span></span><strong>${esc(task.title)}</strong></span>${ic("right", "sm")}</a></li>`).join("")}</ol>${more > 0 ? `<p class="entry-inbox-more">另有 ${more} 件，进入对应工作区查看</p>` : ""}`;
    }
    return `<aside class="entry-inbox"${(cache.projects !== null || cache.error !== null) && total === 0 ? " data-clear" : ""} aria-labelledby="entry-inbox-title"><div class="entry-section-head"><h2 id="entry-inbox-title">等你处理</h2>${total > 0 ? `<span class="entry-count">${total}</span>` : ""}</div>${body}</aside>`;
  }

  function card({item, activity}, themes, {esc, ic, display}) {
    const name = display(item);
    let state, top = ic("right", "sm"), body;
    if (activity === undefined) {
      state = cache.error === null ? "loading" : "missing";
      body = cache.error === null ? `<div class="entry-skeleton" aria-label="读取中"><i></i><i></i></div>` : `<p class="entry-card-note">动态未读取</p>`;
    } else if (activity.error !== undefined) {
      state = "error";
      body = `<p class="entry-card-error">读取失败：${esc(activity.error)}</p>`;
    } else {
      const {counts} = activity;
      const waiting = waitingCount(counts);
      state = waiting > 0 ? "attention" : counts.working > 0 && !activity.unknown ? "busy" : "quiet";
      if (waiting > 0) top = `<span class="entry-card-waiting">${waiting} 件等你</span>`;
      const badges = [];
      if (activity.unknown) badges.push(`<span class="entry-badge" data-kind="unknown">在干活 · 状态未确认</span>`);
      else if (counts.working > 0) badges.push(`<span class="entry-badge" data-kind="working"><i class="entry-pulse" aria-hidden="true"></i>${counts.working} 在干活</span>`);
      for (const [key, kind, label = KIND[kind]] of BADGES) if (counts[key] > 0) badges.push(`<span class="entry-badge" data-kind="${kind}"><b>${counts[key]}</b>${label}</span>`);
      const lines = activity.items.filter(task => !ATTENTION.includes(task.kind)).slice(0, 2);
      const quiet = badges.length === 0 && lines.length === 0;
      body = quiet ? `<p class="entry-card-quiet">安静 · 没有待处理</p>`
        : `${badges.length ? `<div class="entry-badges">${badges.join("")}</div>` : ""}${lines.length ? `<ul class="entry-lines">${lines.map(task => `<li data-kind="${esc(task.kind)}"><span class="entry-line-kind">${KIND[task.kind]}</span><span class="entry-line-title">${esc(task.title)}</span><span class="entry-line-meta">${esc(task.name)}${task.at ? ` · ${ago(task.at)}` : ""}</span></li>`).join("")}</ul>` : ""}`;
    }
    return `<li><a class="entry-card" data-entry="card:${esc(item.name)}" data-state="${state}" style="${CrewTheme.projectStyle(themes, item.theme)}" href="${esc(projectHref(item.name))}"><div class="entry-card-head"><span class="entry-card-logo" aria-hidden="true">${CrewTheme.logo()}</span><strong class="entry-card-name">${esc(name)}</strong><span class="entry-card-top">${top}</span></div><div class="entry-card-body">${body}</div></a></li>`;
  }

  function view(state, helpers) {
    const {esc, ic} = helpers;
    const entries = state.projects.map(item => ({item, activity: cache.projects?.get(item.name)}));
    const loaded = entries.map(entry => entry.activity).filter(activity => activity !== undefined && activity.error === undefined);
    const failure = cache.error === null ? "" : `<p class="entry-failure" role="alert">动态读取失败：${esc(cache.error)}${cache.projects === null ? "" : " · 下面是上次读到的结果"}</p>`;
    const head = `<header class="entry-head"><span class="entry-mark" aria-hidden="true">${CrewTheme.logo()}</span><div class="entry-title"><span class="entry-eyebrow">Crew</span><h1>工作区</h1><p class="entry-summary">${entries.length === 0 ? "<span>还没有工作区</span>" : summary(entries, loaded)}</p></div><nav class="entry-actions" aria-label="快捷入口"><a class="entry-action" href="/assistant.html" title="全局助手">${ic("msg")}<span>全局助手</span></a><a class="entry-action" href="/notes.html" title="想法墙">${ic("pencil")}<span>想法墙</span></a><button type="button" data-local-control class="entry-action entry-action-icon" data-act="settings" title="设置" aria-label="设置">${ic("settings")}</button></nav></header>`;
    if (entries.length === 0) {
      return `<div class="entry">${head}<section class="entry-empty"><span class="entry-empty-mark" aria-hidden="true">${CrewTheme.logo()}</span><h2>还没有工作区</h2><p>在桌面版点“新建工作区”，<br>或在项目目录里运行 <code>crew start</code>。</p></section></div>`;
    }
    return `<div class="entry">${head}${failure}<div class="entry-body">${inbox(entries, loaded, state.themes, helpers)}<section class="entry-workspaces" aria-labelledby="entry-workspaces-title"><div class="entry-section-head"><h2 id="entry-workspaces-title">全部工作区</h2><span class="entry-count">${entries.length}</span></div><ul class="entry-cards">${entries.map(entry => card(entry, state.themes, helpers)).join("")}</ul></section></div></div>`;
  }

  function render(state, helpers) {
    last = {state, helpers};
    refresh(state.projects.map(item => item.name));
    return view(state, helpers);
  }
  window.CrewWorkspaceEntry = {render};
})();
