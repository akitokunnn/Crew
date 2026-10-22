"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const project = new URLSearchParams(location.search).get("project");
  let state = null, view = "all", leaderId = null, readOnlyId = null, mountedChat = null, pollBusy = false, statePromise = Promise.resolve();
  let allTab = "tree", period = "week", usageTab = "tree", usage = null, usageLoading = false, usageRequest = 0;
  let cardId = null, cardReturn = null, rename = false, handoff = null, rightOpen = false, dialogReturn = null;
  let theme = "system", trayCollapsed = false, othersOpen = false, audioContext = null, audioReady = false, paletteItems = [], paletteIndex = 0;
  const decisionsOpen = new Set(), usageCollapsed = new Set(), adoptForms = new Map(), adopted = new Map(), operationIds = new Set();
  const colors = ["#3E7C74", "#8A5A7E", "#A07A3A", "#4F6B8F", "#5E7F55", "#A8604F", "#8C7650", "#5C5F9E", "#A35D6A", "#4F7A8C", "#77773F"];
  const statusText = {working:"在干活", waiting:"等你", idle:"空闲", offline:"离线", closed:"已归档"};
  const paths = {
    search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', plus:'<path d="M12 5v14M5 12h14"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    inbox:'<path d="m4 4-2 10v6h20v-6L20 4H4Z"/><path d="M2 14h6l2 3h4l2-3h6"/>', chart:'<path d="M4 3v18h17M9 16v-5m5 5V7m5 9V3"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon:'<path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/>', auto:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M12 4v13"/>',
    more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>', x:'<path d="m6 6 12 12M6 18 18 6"/>',
    right:'<path d="m9 5 7 7-7 7"/>', down:'<path d="m5 9 7 7 7-7"/>', check:'<path d="m5 12 4 4L19 6"/>',
    code:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14"/>', cloud:'<path d="M6 18a4 4 0 0 1-1-7.8A7 7 0 0 1 18 8a5 5 0 0 1 0 10H6Z"/>',
    bolt:'<path d="m13 2-9 12h7l-1 8 10-13h-7l1-7Z"/>', pencil:'<path d="m16 3 5 5L8 21H3v-5L16 3Zm-3 3 5 5"/>',
    swap:'<path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/>', archive:'<path d="M4 8v13h16V8M3 3h18v5H3V3Zm6 9h6"/>',
    stop:'<rect x="5" y="5" width="14" height="14" rx="2"/>', leave:'<path d="M9 3H3v18h6m6-14 5 5-5 5M8 12h12"/>',
    folder:'<path d="M3 6h7l2 3h9v11H3V6Z"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    device:'<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M10 18h4"/>', file:'<path d="M13 2H5v20h14V8l-6-6Zm0 0v6h6"/>',
    branch:'<circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12m0-5h5a7 7 0 0 0 7-5"/>',
    msg:'<path d="M21 15a3 3 0 0 1-3 3H9l-6 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v9Z"/>', download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>'
  };
  const ic = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
  function tint(name) { let n = 0; for (const c of name) n = (n + c.charCodeAt(0)) % colors.length; return colors[n]; }
  const av = (name, size = 30, round = false) => `<span class="av ${round ? "round" : ""}" style="--s:${size}px;--tint:${tint(name)}">${esc(name.slice(0, 1))}</span>`;
  const st = status => `<span class="st ${esc(status)}"><i class="dot ${esc(status)}"></i>${esc(statusText[status])}</span>`;
  const count = value => Number(value).toLocaleString("zh-CN", {maximumFractionDigits:1});
  const tokens = value => value >= 1000000 ? `${count(value / 1000000)}M` : value >= 1000 ? `${count(value / 1000)}K` : count(value);
  const money = value => `$${Number(value).toFixed(2)}`;
  const time = at => new Date(at).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false});
  const date = at => new Date(at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  function ago(at) { const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000); return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`; }
  const leader = id => state.leaders.find(item => item.session_id === id);
  const member = id => state.members.find(item => item.session_id === id);
  const membersOf = id => state.members.filter(item => item.leader_session_id === id);
  const pendingOf = id => state.decisions.filter(item => item.leader_session_id === id || item.session_id === id);
  function queryPath(path) { const url = new URL(path, location.origin); if (project !== null) url.searchParams.set("project", project); return url.pathname + url.search; }
  async function api(path, data) {
    const response = await fetch(queryPath(path), data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Crew":"1"}, body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }
  function toast(message, undo = null) {
    const node = document.createElement("div"); node.className = "toast show"; node.setAttribute("role", "status"); node.textContent = message;
    if (undo !== null) { const button = document.createElement("button"); button.type = "button"; button.textContent = "撤销"; button.onclick = async () => { button.disabled = true; try { await undo(); node.remove(); } catch (error) { button.disabled = false; toast(error.message); } }; node.append(button); }
    $("toast-root").replaceChildren(node); setTimeout(() => node.remove(), undo === null ? 5000 : 12000);
  }
  function replace(node, html) {
    if (node.dataset.render === html) return;
    const focused = node.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset.preserve;
    const focusData = focused === null ? null : {...focused.dataset};
    const focusForm = focused?.form;
    const selection = focused && "selectionStart" in focused ? [focused.selectionStart, focused.selectionEnd] : null;
    const values = new Map(Array.from(node.querySelectorAll("[data-preserve]")).filter(item => "value" in item).map(item => [item.dataset.preserve, item.value]));
    const scroll = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-list")).map(item => [item.className, item.scrollTop]);
    node.innerHTML = html; node.dataset.render = html;
    for (const item of node.querySelectorAll("[data-preserve]")) if (values.has(item.dataset.preserve) && "value" in item) item.value = values.get(item.dataset.preserve);
    for (const [name, top] of scroll) { const item = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-list")).find(item => item.className === name); if (item) item.scrollTop = top; }
    if (focusKey !== undefined) { const item = Array.from(node.querySelectorAll("[data-preserve]")).find(item => item.dataset.preserve === focusKey); if (item) { item.focus({preventScroll:true}); if (selection) item.setSelectionRange(...selection); } }
    else if (focused !== null) {
      const item = Array.from(node.querySelectorAll(focused.tagName)).find(item => item.id === focused.id && item.getAttribute("name") === focused.getAttribute("name") && item.getAttribute("type") === focused.getAttribute("type") && Object.entries(focusData).every(([key, value]) => item.dataset[key] === value) && (focusForm === null || focusForm === undefined || item.form !== null && item.form.id === focusForm.id && Object.entries(focusForm.dataset).every(([key, value]) => item.form.dataset[key] === value)));
      if (item) item.focus({preventScroll:true});
    }
  }
  async function action(key, fn) { if (operationIds.has(key)) return; operationIds.add(key); try { await fn(); } catch (error) { toast(error.message); } finally { operationIds.delete(key); } }
  function quotas() { return [["Claude 每周", state.limits.claude.seven_day], ["Claude 5 小时", state.limits.claude.five_hour], ["Codex 每周", state.limits.codex]]; }
  function quotaRow(label, limit) { return `<div class="qrow"><span>${label}</span><span class="pct">${limit === null ? "未采集" : `${count(limit.used_percent)}%`}</span>${limit === null ? "" : `<span class="bar"><i class="${limit.used_percent >= 80 ? "hi" : ""}" style="width:${limit.used_percent}%"></i></span>`}</div>`; }
  function applyTheme() { if (theme === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = theme; }
  function renderSidebar() {
    const themeIcon = {system:"auto", light:"sun", dark:"moon"}[theme], themeName = {system:"跟随系统", light:"浅色", dark:"深色"}[theme];
    replace($("sidebar"), `<div class="brand"><img class="brand-logo" src="/logo.svg" alt=""><div><div class="proj">${esc(state.project.name)}</div><div class="sub">Crew 工作区</div></div></div>
      <button class="search-btn" data-act="palette">${ic("search", "sm")}<span>找 leader 或下属</span><span class="kbd">Ctrl K</span></button>
      <button class="nav-item ${view === "all" ? "active" : ""}" data-act="view" data-view="all">${ic("grid")}<span>全部</span></button>
      <div class="sec-label">Leader</div>${state.leaders.map(item => `<button class="leader-item ${view === "chat" && leaderId === item.session_id && readOnlyId === null ? "active" : ""}" data-act="leader" data-id="${esc(item.session_id)}">${av(item.name, 32)}<span class="meta"><span class="nm">${esc(item.name)}${item.busy ? '<span class="typing" title="正在回复"><i></i><i></i><i></i></span>' : ""}</span><span class="goal">${esc(item.goal)}</span></span><span class="badges">${item.pending > 0 ? `<span class="b-pending" title="${item.pending} 件等你拍板">${item.pending}</span>` : ""}${item.unread > 0 ? `<span class="b-unread" title="未读">${item.unread}</span>` : ""}</span></button>`).join("")}
      <button class="leader-item add-leader" data-act="modal-leader"><span class="plus">${ic("plus", "sm")}</span><span>起 leader</span></button>
      <div class="sb-mid"><button class="nav-item ${view === "adopt" ? "active" : ""}" data-act="view" data-view="adopt">${ic("inbox")}<span>待收编</span><span class="count">${state.candidates.running.length} 个在运行</span></button><button class="nav-item ${view === "usage" ? "active" : ""}" data-act="view" data-view="usage">${ic("chart")}<span>用量</span></button></div>
      <div class="sb-foot"><button class="quota" data-act="view" data-view="usage" aria-label="查看额度和用量">${quotas().map(([label, limit]) => quotaRow(label, limit)).join("")}</button><div class="foot-row"><button class="switch ${state.notification.manual_away ? "on" : ""}" data-act="away" role="switch" aria-checked="${state.notification.manual_away}"><span class="track"></span><span>我已离开</span></button><span class="gap"></span><button class="icon-btn" data-act="theme" title="${themeName}" aria-label="主题：${themeName}">${ic(themeIcon)}</button></div>${state.notification.away ? '<div class="away-note">离开中：待拍板会推到飞书</div>' : ""}${state.notification.error === null ? "" : `<div class="error-line">${esc(state.notification.error)}</div>`}</div>`);
  }
  function decisionCard(item) {
    const opened = decisionsOpen.has(item.id);
    return `<div class="dc ${opened ? "open" : ""}" data-dc="${item.id}"><button class="dc-top" data-act="decision-toggle" data-id="${item.id}" aria-expanded="${opened}">${av(item.asker, 22, true)}<span class="q">${esc(item.question)}</span><span class="from">${esc(item.asker)}</span>${item.recommendation === null || item.recommendation === "" ? "" : `<span class="recp">推荐 ${esc(item.recommendation)}</span>`}<span class="chev">${ic("right", "sm")}</span></button>
      <div class="dc-body"><div class="opts">${item.options.map((option, index) => { const key = String.fromCharCode(65 + index), recommended = item.recommendation === key || item.recommendation === option; return `<button class="opt" data-act="answer-option" data-id="${item.id}" data-option="${index}"><span class="k">${key}</span><span class="t">${esc(option.replace(/^[A-Z][.、．]\s*/, ""))}</span>${recommended ? '<span class="rec">推荐</span>' : ""}</button>`; }).join("")}</div><form class="free" data-answer-form="${item.id}"><input data-preserve="answer-${item.id}" name="answer" placeholder="或者直接说你的想法，回车发送" required aria-label="答复 ${esc(item.asker)}"><button class="icon-btn" type="submit" title="发送答复">${ic("check", "sm")}</button></form></div></div>`;
  }
  function codexMarkup(item, cls = "cx") {
    const running = item.status === "running";
    return `<div class="${cls}">${ic("bolt", "xs")}<span class="${cls === "cx" ? "n" : "grow"}">${esc(item.task_name)}</span><span class="s">${running ? `<span class="spin" style="width:10px;height:10px"></span>${Math.max(0, Math.floor((Date.now() - new Date(item.started_at)) / 60000))} 分钟` : `${ic("check", "xs ok")}${item.ended_at === null ? esc(item.status) : time(item.ended_at)}`}</span></div>`;
  }
  function memberNode(item) {
    const where = item.where === "bg" ? "cloud" : item.where === "vscode" ? "code" : "clock";
    return `<button class="node click ${cardId === item.session_id ? "sel" : ""}" data-act="card" data-id="${esc(item.session_id)}"><span class="node-top">${av(item.name, 26, true)}<span class="nm">${esc(item.name)}<span class="where" title="${item.where === "bg" ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}">${ic(where, "xs")}</span></span>${st(item.status)}</span><span class="now">${esc(item.task)}</span>${item.codex.length > 0 || item.codex_older > 0 ? `<span class="cx-list">${item.codex.map(item => codexMarkup(item)).join("")}${item.codex_older > 0 ? `<span class="cx older">${ic("more", "xs")}<span class="n">更早 ${item.codex_older} 个</span></span>` : ""}</span>` : ""}</button>`;
  }
  function leaderNode(item, clickable) {
    return `<${clickable ? "button" : "div"} class="node ${clickable ? "click" : ""} leader-node" ${clickable ? `data-act="leader" data-id="${esc(item.session_id)}"` : ""}><span class="node-top">${av(item.name, 30)}<span class="nm">${esc(item.name)}<span class="tag" style="background:var(--hover);color:var(--text-3)">leader</span></span>${item.pending > 0 ? `<span class="b-pending" style="margin-left:auto">${item.pending}</span>` : st(item.status)}</span><span class="now" style="margin-left:39px">${esc(item.goal)}</span></${clickable ? "button" : "div"}>`;
  }
  function renderRight() {
    if (view !== "chat") { $("right-panel").innerHTML = ""; delete $("right-panel").dataset.render; return; }
    const item = leader(leaderId), members = membersOf(leaderId);
    replace($("right-panel"), `<div class="head"><div><div class="title" style="font-size:14.5px">${esc(item.name)} 组</div><div class="sub">${members.length} 个下属</div></div><div class="right"><button class="btn ghost" data-act="view" data-view="adopt" style="padding:5px 9px;font-size:12.5px">${ic("plus", "sm")}收编</button><button class="icon-btn rp-close" data-act="right-close" aria-label="关闭架构图">${ic("x")}</button></div></div><div class="tree">${leaderNode(item, false)}<div class="members">${members.map(memberNode).join("")}</div><div class="rp-note">点下属看卡片。${ic("code", "xs")} 开在 VS Code，${ic("cloud", "xs")} 后台运行。</div></div>`);
    $("right-panel").classList.toggle("show", rightOpen);
    $("rp-scrim").classList.toggle("show", rightOpen);
  }
  function renderChat() {
    const item = readOnlyId === null ? leader(leaderId) : member(readOnlyId), group = leader(leaderId), members = membersOf(leaderId), pending = pendingOf(leaderId);
    const identity = `${readOnlyId === null ? "leader" : "history"}:${item.session_id}`;
    if (mountedChat !== identity) {
      CrewChat.unmount();
      $("main").innerHTML = '<div id="chat-head"></div><div id="decision-tray"></div><div id="chat-host" class="chat-host"></div>';
      delete $("main").dataset.render;
      mountedChat = identity;
      CrewChat.mount({host:$("chat-host"), session:item, readonly:readOnlyId !== null, state, api, toast});
    }
    replace($("chat-head"), `<div class="head">${av(item.name, 34)}<div class="heading-meta"><div class="title">${esc(item.name)}${readOnlyId === null ? "" : " · 对话记录"}</div><div class="sub"><span>${esc(readOnlyId === null ? item.goal : `归 ${group.name} · 只读`)}</span><span class="sep"></span><span>${members.length} 个下属 · ${members.filter(item => item.status === "working").length} 个在干活</span></div></div><div class="right">${readOnlyId === null ? "" : `<button class="btn ghost" data-act="leader" data-id="${esc(leaderId)}">回到 leader</button>`}<button class="icon-btn rp-toggle" data-act="right-open" title="本组架构图" aria-label="本组架构图">${ic("grid")}</button></div></div>`);
    replace($("decision-tray"), readOnlyId !== null || pending.length === 0 ? "" : `<div class="tray ${trayCollapsed ? "collapsed" : ""}"><div class="tray-in"><button class="tray-head" data-act="tray" aria-expanded="${!trayCollapsed}">${ic("down", "sm chev")}等你拍板 · ${pending.length}</button><div class="tray-list">${pending.map(decisionCard).join("")}</div></div></div>`);
    CrewChat.update(state);
  }
  function empty(message) { return `<div class="empty">${ic("check")}<div>${message}</div></div>`; }
  function renderAll() {
    const working = state.members.filter(item => item.status === "working"), pending = state.decisions;
    const chip = (tab, icon, label, amount) => `<button class="pill tab ${allTab === tab ? "on" : ""}" data-act="all-tab" data-tab="${tab}">${icon}${amount === null ? "" : `<b>${amount}</b>`}${label}</button>`;
    let body;
    if (allTab === "tree") body = `<div class="lead-grid">${state.leaders.map(item => `<div class="lead-col">${leaderNode(item, true)}<div class="members">${membersOf(item.session_id).map(memberNode).join("")}</div></div>`).join("")}</div>`;
    else if (allTab === "pending") {
      const grouped = new Set();
      body = state.leaders.map(item => { const list = pendingOf(item.session_id); list.forEach(item => grouped.add(item.id)); return list.length === 0 ? "" : `<section class="ov-group"><div class="ov-head">${av(item.name, 26)}<span class="ov-name">${esc(item.name)}</span><span class="ov-count">${list.length} 件</span><button class="btn ghost ov-go" data-act="leader" data-id="${esc(item.session_id)}">进入对话 ${ic("right", "sm")}</button></div><div class="ov-list">${list.map(decisionCard).join("")}</div></section>`; }).join("");
      const ungrouped = pending.filter(item => !grouped.has(item.id));
      if (ungrouped.length > 0) body += `<section class="ov-group"><div class="ov-head"><span class="ov-name">未分组</span><span class="ov-count">${ungrouped.length} 件</span></div><div class="ov-list">${ungrouped.map(decisionCard).join("")}</div></section>`;
      if (pending.length === 0) body = empty("没有等你拍板的事");
    } else body = working.length === 0 ? empty("没有正在干活的下属") : `<div class="list">${working.map(item => { const group = leader(item.leader_session_id); return `<button class="li click" data-act="card" data-id="${esc(item.session_id)}">${av(item.name, 32, true)}<span class="grow"><span class="ttl">${esc(item.name)} <span class="where">${ic(item.where === "bg" ? "cloud" : "code", "xs")}</span><span class="li-sub">归 ${group === undefined ? "未分组" : esc(group.name)}</span></span><span class="li-now">${esc(item.task)}</span><span class="sm">${item.codex.filter(item => item.status === "running").map(item => `<span>${ic("bolt", "xs")} ${esc(item.task_name)} · <span class="spin" style="width:9px;height:9px;display:inline-block"></span> ${Math.floor((Date.now() - new Date(item.started_at)) / 60000)} 分钟</span>`).join("")}</span></span>${st(item.status)}</button>`; }).join("")}</div>`;
    replace($("main"), `<div class="head big"><div><div class="title">全部</div><div class="sub">${esc(state.project.name)} 的 ${state.leaders.length} 个 leader，${state.members.length} 个下属</div></div></div><div class="page"><div class="page-in"><div class="summary">${chip("tree", ic("grid", "sm"), "架构图", null)}${chip("pending", '<i class="dot waiting"></i>', " 件等你拍板", pending.length)}${chip("working", '<i class="dot working"></i>', " 个在干活", working.length)}<button class="pill tab" data-act="view" data-view="adopt">${ic("inbox", "sm")}<b>${state.candidates.running.length}</b> 个会话待收编 ${ic("right", "xs")}</button></div>${body}</div></div>`);
  }
  function renderAdopt() {
    const row = (item, bucket) => {
      const done = adopted.get(item.session_id), form = adoptForms.get(item.session_id), open = form !== undefined && done === undefined;
      return `<div class="li">${bucket === "running" ? `<i class="dot ${esc(item.status)}" style="margin:0 4px"></i>` : '<span style="width:15px"></span>'}<div class="grow"><div class="ttl">${esc(item.title)}</div><div class="sm"><span>${ic("folder", "xs")} ${esc(item.rel_dir)}</span><span>${ic("clock", "xs")} ${ago(item.last_active)}</span>${item.crew_name === null ? "" : `<span>${esc(item.crew_name)}</span>`}</div></div>${done === undefined ? `<button class="btn ${open ? "ghost" : ""}" data-act="adopt-open" data-id="${esc(item.session_id)}">${open ? "取消" : "收编"}</button>` : `<span class="st working">${ic("check", "sm")}已收编到 ${esc(done.group)}，它下次说话时会收到通知</span>`}</div>${open ? `<form class="adopt-form" data-adopt-form="${esc(item.session_id)}"><label class="lbl" for="adopt-name-${esc(item.session_id)}">叫它</label><input id="adopt-name-${esc(item.session_id)}" data-preserve="adopt-name-${esc(item.session_id)}" name="name" value="${esc(form.name)}" placeholder="名字" required><span class="lbl">归到</span><div class="lchips">${state.leaders.map(group => `<button type="button" class="lchip ${form.group === group.name ? "on" : ""}" data-act="adopt-leader" data-id="${esc(item.session_id)}" data-group="${esc(group.name)}">${av(group.name, 20)}${esc(group.name)}</button>`).join("")}</div><button class="btn primary" type="submit" style="margin-left:auto" ${state.leaders.length === 0 ? "disabled" : ""}>确认收编</button>${state.leaders.length === 0 ? '<span class="error-line">先起一个 leader，再收编到它的组。</span>' : ""}</form>` : ""}`;
    };
    const list = bucket => { const items = state.candidates[bucket].slice(); for (const result of adopted.values()) if (result.bucket === bucket && !items.some(item => item.session_id === result.item.session_id)) items.push(result.item); return items; };
    replace($("main"), `<div class="head big"><div><div class="title">待收编</div><div class="sub">${esc(state.project.name)} 下还没归到 leader 的会话</div></div></div><div class="page"><div class="page-in" style="max-width:860px">${state.vscode_error === null ? "" : `<p class="error-line">${esc(state.vscode_error)}</p>`}<div class="group-h"><i class="dot working"></i>正在运行 · ${state.candidates.running.length}</div><div class="list">${list("running").map(item => row(item, "running")).join("")}</div><div class="group-h">${ic("archive", "sm")}没在运行、也没归档 · ${state.candidates.others.length}</div><div class="list">${othersOpen ? `${list("others").map(item => row(item, "others")).join("")}<button class="more-btn" data-act="others">${ic("down", "sm")}收起</button>` : `<button class="more-btn" data-act="others">${ic("right", "sm")}展开 ${state.candidates.others.length} 个（跟 VS Code 会话列表一致，已归档的不在这里）</button>`}</div><div class="group-h">${ic("leave", "sm")}退了组的 · ${state.candidates.left.length}</div><div class="list">${list("left").map(item => row(item, "left")).join("")}</div><p class="note">也可以在那个 VS Code 会话里说「收编到某某」。</p></div></div>`);
  }
  function quotaCard(label, limit) {
    return `<div class="qcard"><div class="lbl"><span>${label}</span><span>账户</span></div>${limit === null ? '<div class="big" style="font-size:22px">未采集</div>' : `<div class="big">${count(limit.used_percent)}%<small>已用</small></div><div class="bar"><i style="width:${limit.used_percent}%;${limit.used_percent >= 80 ? "background:var(--warn)" : ""}"></i></div><div class="rs">${date(limit.resets_at)} 重置 · ${time(limit.captured_at)} 采集${limit.expired ? " · 采集已过期" : ""}</div>`}</div>`;
  }
  function renderUsage() {
    let table = '<p class="note">正在读取用量…</p>', total = "";
    if (usage !== null) {
      const totalTokens = usage.tree.reduce((sum, row) => sum + row.tokens, 0), totalCost = usage.tree.reduce((sum, row) => sum + row.cost, 0);
      total = `合计 ${tokens(totalTokens)} token · 折合 ${money(totalCost)}`;
      const share = amount => `<div class="share">${count(amount)}%<span class="b"><i style="width:${Math.min(100, amount)}%"></i></span></div>`;
      if (usageTab === "tree") table = `<table class="utable"><thead><tr><th>谁</th><th class="num">Token</th><th class="num">折合</th><th class="num">占比</th></tr></thead><tbody>${usage.tree.map(row => {
        const key = row.leader_session_id === null ? "ungrouped" : row.leader_session_id, open = !usageCollapsed.has(key);
        const selfShare = totalTokens === 0 ? 0 : row.self.tokens / totalTokens * 100;
        return `<tr class="grp"><td><button class="cell-name table-toggle" data-act="usage-fold" data-id="${esc(key)}" aria-expanded="${open}">${ic(open ? "down" : "right", "xs")}${av(row.name, 22)}${esc(row.name)}</button></td><td class="num">${tokens(row.tokens)}</td><td class="num">${money(row.cost)}</td><td class="num">${share(row.share)}</td></tr>${open ? `${row.leader_session_id === null ? "" : `<tr class="sub"><td><span class="cell-name">${av(row.name, 20, true)}${esc(row.name)}（leader 本身）</span></td><td class="num">${tokens(row.self.tokens)}</td><td class="num">${money(row.self.cost)}</td><td class="num">${share(selfShare)}</td></tr>`}${row.members.map(item => `<tr class="sub"><td><span class="cell-name">${av(item.name, 20, true)}${esc(item.name)}</span></td><td class="num">${tokens(item.tokens)}</td><td class="num">${money(item.cost)}</td><td class="num">${share(item.share)}</td></tr>${item.codex_runs > 0 ? `<tr class="cxr"><td><span class="cell-name">${ic("bolt", "xs")}Codex ${item.codex_runs} 次</span></td><td class="num">${tokens(item.codex_tokens)}</td><td class="num">已计入上一行</td><td></td></tr>` : ""}`).join("")}` : ""}`;
      }).join("")}</tbody></table>`;
      else if (usageTab === "daily") table = `<table class="utable"><thead><tr><th>日期</th><th>会话</th><th class="num">Token</th><th class="num">折合</th></tr></thead><tbody>${usage.daily.map(row => `<tr><td>${esc(row.date)}</td><td>${esc(row.display_name)}</td><td class="num">${tokens(row.total_tokens)}</td><td class="num">${money(row.cost_usd)}</td></tr>`).join("")}</tbody></table>`;
      else table = `<table class="utable"><thead><tr><th>模型</th><th>来源</th><th class="num">输入</th><th class="num">输出</th><th class="num">合计</th></tr></thead><tbody>${usage.models.map(row => `<tr><td>${esc(row.model)}</td><td>${esc(row.source)}</td><td class="num">${tokens(row.input_tokens)}</td><td class="num">${tokens(row.output_tokens)}</td><td class="num">${tokens(row.total_tokens)}</td></tr>`).join("")}</tbody></table>`;
    }
    replace($("main"), `<div class="head big"><div><div class="title">用量</div><div class="sub">${esc(state.project.name)} · ${{today:"今天", week:"本周", all:"全部"}[period]}</div></div><div class="right" style="gap:10px"><div class="seg">${[["today", "今天"], ["week", "本周"], ["all", "全部"]].map(([key, name]) => `<button class="${period === key ? "on" : ""}" data-act="usage-period" data-period="${key}">${name}</button>`).join("")}</div><a class="btn" href="${esc(queryPath(`/api/usage/export?period=${period}`))}" download>${ic("download", "sm")}导出</a></div></div><div class="page"><div class="page-in"><div class="qcards">${quotas().map(([label, limit]) => quotaCard(label, limit)).join("")}</div><div class="usage-tabs"><div class="seg">${[["tree", "按结构"], ["daily", "按天"], ["models", "按模型"]].map(([key, name]) => `<button class="${usageTab === key ? "on" : ""}" data-act="usage-tab" data-tab="${key}">${name}</button>`).join("")}</div><span style="font-size:12.5px;color:var(--text-3)">${total}</span></div>${table}<div class="note">折合金额按 API 价换算，只用于比较；额度是账户级；界面字体 HarmonyOS Sans（华为，按其许可证随附）</div></div></div>`);
  }
  async function loadUsage() {
    const sequence = ++usageRequest; usageLoading = true;
    try { const result = await api(`/api/usage?period=${period}`); if (sequence === usageRequest) { usage = result; if (view === "usage") renderUsage(); } }
    catch (error) { if (sequence === usageRequest) { toast(error.message); if (view === "usage") { const note = $("main").querySelector(".page-in"); note.append(Object.assign(document.createElement("p"), {className:"error-line", textContent:error.message})); } } }
    finally { if (sequence === usageRequest) usageLoading = false; }
  }
  function focusDescriptor(node) { if (node === null || node === document.body) return null; return {node, act:node.dataset.act, id:node.dataset.id, view:node.dataset.view}; }
  function returnFocus(descriptor) {
    if (descriptor === null) return;
    if (descriptor.node.isConnected) { descriptor.node.focus({preventScroll:true}); return; }
    const target = Array.from(document.querySelectorAll("[data-act]")).find(item => item.dataset.act === descriptor.act && item.dataset.id === descriptor.id && item.dataset.view === descriptor.view);
    if (target) target.focus({preventScroll:true});
  }
  function openCard(id) { cardReturn = focusDescriptor(document.activeElement); cardId = id; rename = false; handoff = null; renderCard(); renderRight(); requestAnimationFrame(() => $("card").querySelector('[data-act="card-close"]').focus({preventScroll:true})); }
  function closeCard() { cardId = null; rename = false; handoff = null; $("card").classList.remove("show"); $("card-back").classList.remove("show"); hideMenu(); renderRight(); returnFocus(cardReturn); }
  function renderCard() {
    if (cardId === null) return;
    const item = member(cardId);
    if (item === undefined) { closeCard(); return; }
    const group = leader(item.leader_session_id), background = item.where === "bg", canStop = background && item.status === "working";
    const stopReason = background ? "这一轮没在工作" : item.where === "vscode" ? "开在 VS Code 里，去那边停" : "会话没在运行";
    const name = rename ? `<form class="rename-form" id="rename-form"><input name="name" data-preserve="rename-name" value="${esc(item.name)}" required aria-label="新名字"><button class="icon-btn" type="submit" aria-label="保存名字">${ic("check")}</button><button class="icon-btn" type="button" data-act="rename-cancel" aria-label="取消改名">${ic("x")}</button></form>` : esc(item.name);
    replace($("card"), `<div class="card-head">${av(item.name, 44, true)}<div style="min-width:0;flex:1"><div class="nm">${name}</div><div class="meta">${st(item.status)}<span>${ic(background ? "cloud" : item.where === "vscode" ? "code" : "clock", "xs")} ${background ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}</span><span>归 ${group === undefined ? "未分组" : esc(group.name)}</span></div></div><button class="icon-btn" data-act="card-close" aria-label="关闭下属卡片">${ic("x")}</button></div>
      <div class="card-actions"><button class="btn primary" data-act="open-vscode" ${item.status === "working" ? 'disabled title="会话正在工作，请等它空闲后再打开"' : ""}>${ic("code", "sm")}在 VS Code 打开</button><span class="gap"></span><button class="icon-btn" data-act="rename" title="改名" aria-label="改名">${ic("pencil")}</button><button class="icon-btn" data-act="move-menu" title="换组" aria-label="换组">${ic("swap")}</button><button class="icon-btn" data-act="archive" title="归档" aria-label="归档">${ic("archive")}</button><button class="icon-btn" data-act="stop" ${canStop ? "" : "disabled"} title="${canStop ? "停下这一轮" : stopReason}" aria-label="停下这一轮">${ic("stop")}</button><button class="icon-btn" data-act="more-menu" title="更多" aria-label="更多">${ic("more")}</button></div>
      ${handoff === null ? "" : `<div class="step"><div class="row"><span class="n">${ic("check", "xs")}</span><span>已打开目录，等 VS Code 窗口准备好<br><code>${esc(handoff.cwd)}</code></span></div><div class="row"><span class="n todo">2</span><span style="flex:1">窗口好了以后，${handoff.needs_confirmation ? "停掉后台或终端这一份，在 VS Code 里接上同一个会话" : "在 VS Code 里接上同一个会话"}<br><button class="btn primary" data-act="handoff-go" style="margin-top:8px">窗口好了，接上</button><button class="btn ghost" data-act="handoff-cancel" style="margin-top:8px">先不了</button></span></div></div>`}
      <div class="card-body"><div class="sec"><h4>负责范围</h4><p>${item.scope === null || item.scope === "" ? "还没写" : esc(item.scope)}</p></div><div class="sec"><h4>现在在做</h4><p>${esc(item.task)}</p></div><div class="sec"><h4>最近进展</h4>${item.progress.length === 0 ? '<p class="note">还没写</p>' : `<div class="tl">${item.progress.map(progress => `<div class="tl-i"><span class="t" title="${esc(date(progress.at))}">${time(progress.at)}</span><span>${esc(progress.text)}</span></div>`).join("")}</div>`}</div><div class="sec"><h4>派出去的 Codex</h4><div class="chips">${item.codex.map(item => codexMarkup(item, "chip")).join("")}${item.codex_older > 0 ? `<div class="chip" style="color:var(--text-3)">更早 ${item.codex_older} 个（已完成）</div>` : ""}${item.codex.length === 0 && item.codex_older === 0 ? '<p class="note">还没有</p>' : ""}</div></div><div class="sec"><h4>设备和文件</h4><div class="chips">${item.leases.map(lease => `<div class="chip" title="${esc(lease.purpose)} · ${date(lease.taken_at)} 至 ${lease.until_at === null ? "未写归还时间" : date(lease.until_at)}">${ic("device", "xs")}${esc(lease.resource)}<span class="grow"></span>${esc(lease.purpose)}</div>`).join("")}${item.claims.map(claim => `<div class="chip" title="${esc(claim.note)}">${ic("file", "xs")}<code>${esc(claim.pattern)}</code></div>`).join("")}${item.leases.length === 0 && item.claims.length === 0 ? '<p class="note">没有占用的设备和文件</p>' : ""}</div></div></div>
      <div class="card-foot">${item.web_chat ? `<button class="text-link" data-act="member-history">${ic("msg", "xs")}看对话记录</button>` : ""}<span class="gap"></span>${ic("branch", "xs")}<code title="${esc(item.branch)}">${esc(item.branch)}</code><code title="${esc(item.session_id)}">${esc(item.session_id)}</code></div>`);
    $("card").classList.add("show"); $("card-back").classList.add("show");
  }
  function showMenu(anchor, html) {
    const menu = $("menu"), rect = anchor.getBoundingClientRect(); menu.innerHTML = html; menu.classList.add("show");
    menu.style.left = `${Math.max(8, Math.min(innerWidth - menu.offsetWidth - 12, rect.right - menu.offsetWidth))}px`;
    menu.style.top = `${Math.min(innerHeight - menu.offsetHeight - 8, rect.bottom + 6)}px`;
    menu.querySelector("button")?.focus({preventScroll:true});
  }
  function hideMenu() { $("menu").classList.remove("show"); }
  function closeBackdrop() { $("backdrop").classList.remove("show"); $("backdrop").innerHTML = ""; returnFocus(dialogReturn); }
  function openModalLeader() {
    dialogReturn = focusDescriptor(document.activeElement);
    $("backdrop").innerHTML = `<form class="modal" id="leader-form" role="dialog" aria-modal="true" aria-labelledby="leader-title"><h3 id="leader-title">起一个 leader</h3><p class="d">它在后台运行，你在这里跟它聊。下属可以以后再收编进来，它也能自己开新下属。</p><div class="field"><label for="leader-name">叫它</label><input id="leader-name" name="name" placeholder="比如：打包" required></div><div class="field"><label for="leader-task">它要管什么</label><textarea id="leader-task" name="task" rows="3" placeholder="一两句话，比如：安卓打包和推包流程跑通，能无人值守出包" required></textarea></div><p class="error-line" id="leader-error"></p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="submit">起 leader</button></div></form>`;
    $("backdrop").classList.add("show"); $("leader-name").focus();
  }
  function openPalette() {
    dialogReturn = focusDescriptor(document.activeElement); paletteIndex = 0;
    $("backdrop").innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-labelledby="palette-title"><span class="sr-only" id="palette-title">快速查找</span><div class="in">${ic("search")}<input id="palette-query" placeholder="输入名字，回车打开" aria-label="搜索 leader、下属或操作"><span class="kbd">Esc</span></div><div class="res" id="palette-results"></div></div>`;
    $("backdrop").classList.add("show"); renderPalette(""); $("palette-query").focus();
    $("palette-query").oninput = event => { paletteIndex = 0; renderPalette(event.target.value); };
    $("palette-query").onkeydown = event => {
      if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); paletteIndex = Math.max(0, Math.min(paletteItems.length - 1, paletteIndex + (event.key === "ArrowDown" ? 1 : -1))); renderPalette(event.target.value); $("palette-results").querySelector(".on")?.scrollIntoView({block:"nearest"}); }
      if (event.key === "Enter" && !event.isComposing && paletteItems[paletteIndex]) { event.preventDefault(); const item = paletteItems[paletteIndex]; closeBackdrop(); item.go(); }
    };
  }
  function renderPalette(term) {
    const items = state.leaders.map(item => ({group:"Leader", key:item.name + item.goal, html:`${av(item.name, 22)}${esc(item.name)}<span class="sm">${esc(item.goal)}</span>`, go:() => navigate("chat", item.session_id)}))
      .concat(state.members.map(item => ({group:"下属", key:item.name + item.task, html:`${av(item.name, 22, true)}${esc(item.name)}<span class="sm">${esc(item.group)} · ${statusText[item.status]}</span>`, go:() => { if (item.leader_session_id !== null) navigate("chat", item.leader_session_id); openCard(item.session_id); }})))
      .concat([{group:"操作", key:"起 leader 新建", html:`${ic("plus")}起 leader`, go:openModalLeader}, {group:"操作", key:"全部 架构图", html:`${ic("grid")}全部`, go:() => navigate("all")}, {group:"操作", key:"待收编 收编", html:`${ic("inbox")}待收编`, go:() => navigate("adopt")}, {group:"操作", key:"用量 额度", html:`${ic("chart")}用量`, go:() => navigate("usage")}]);
    paletteItems = items.filter(item => item.key.toLowerCase().includes(term.trim().toLowerCase()));
    let group = "";
    $("palette-results").innerHTML = paletteItems.length === 0 ? '<p class="note" style="padding:12px">没有找到匹配项</p>' : paletteItems.map((item, index) => { const title = group === item.group ? "" : `<div class="grp">${item.group}</div>`; group = item.group; return `${title}<button class="it ${index === paletteIndex ? "on" : ""}" data-act="palette-go" data-index="${index}">${item.html}</button>`; }).join("");
  }
  function syncRoute() {
    const hash = view === "chat" ? readOnlyId === null ? `leader=${encodeURIComponent(leaderId)}` : `history=${encodeURIComponent(readOnlyId)}&leader=${encodeURIComponent(leaderId)}` : view;
    if (location.hash.slice(1) !== hash) history.replaceState(null, "", `${location.pathname}${location.search}#${hash}`);
  }
  function navigate(next, id = null, readonly = null) {
    if (view === "chat" && (next !== "chat" || leaderId !== id || readOnlyId !== readonly)) { CrewChat.unmount(); mountedChat = null; }
    view = next; leaderId = id; readOnlyId = readonly; rightOpen = false;
    if (next !== "chat") { $("right-panel").classList.remove("show"); $("rp-scrim").classList.remove("show"); }
    render(); syncRoute();
    if (view === "usage" && usage === null && !usageLoading) loadUsage();
  }
  function readRoute() {
    const hash = location.hash.slice(1), params = new URLSearchParams(hash);
    if (params.has("history")) { readOnlyId = params.get("history"); leaderId = params.get("leader"); view = "chat"; }
    else if (params.has("leader")) { leaderId = params.get("leader"); readOnlyId = null; view = "chat"; }
    else if (["all", "adopt", "usage"].includes(hash)) { view = hash; readOnlyId = null; }
    else if (state.leaders.length > 0) { leaderId = state.leaders[0].session_id; readOnlyId = null; view = "chat"; }
    else view = "all";
    if (view === "chat" && leader(leaderId) === undefined) { view = "all"; leaderId = null; readOnlyId = null; }
  }
  function render() {
    $("app").classList.toggle("wide", view !== "chat"); renderSidebar();
    if (view === "chat") renderChat(); else { if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; } if (view === "all") renderAll(); if (view === "adopt") renderAdopt(); if (view === "usage") renderUsage(); }
    renderRight(); renderCard(); document.title = `(${state.decisions.length}) ${state.project.name} · Crew`;
  }
  function unlockAudio() { if (audioReady) return; audioReady = true; audioContext = new AudioContext(); void audioContext.resume(); }
  function beep() {
    if (!audioReady || audioContext.state !== "running") return;
    const oscillator = audioContext.createOscillator(), gain = audioContext.createGain(), at = audioContext.currentTime;
    oscillator.type = "sine"; oscillator.frequency.setValueAtTime(660, at); oscillator.frequency.exponentialRampToValueAtTime(520, at + .14);
    gain.gain.setValueAtTime(0, at); gain.gain.linearRampToValueAtTime(.055, at + .012); gain.gain.exponentialRampToValueAtTime(.001, at + .19);
    oscillator.connect(gain); gain.connect(audioContext.destination); oscillator.start(at); oscillator.stop(at + .2);
  }
  async function loadState(fresh = false) {
    if (pollBusy) { await statePromise; if (fresh) return loadState(true); return; }
    pollBusy = true;
    let complete;
    statePromise = new Promise(resolve => { complete = resolve; });
    try {
      const result = await api("/api/state"), previous = state; state = result;
      if (project === null) { $("app").className = "workspace-entry"; $("sidebar").hidden = true; $("right-panel").hidden = true; replace($("main"), `<div class="workspace-home"><img src="/logo.svg" width="42" height="42" alt="Crew"><h1>Crew 工作区</h1><p class="note">选一个项目，继续聊。</p><div class="list">${state.projects.map(item => `<a class="li" href="/?project=${encodeURIComponent(item.name)}"><span class="grow">${esc(item.name)}</span>${ic("right")}</a>`).join("")}</div></div>`); document.title = "工作区 · Crew"; return; }
      if (previous === null) readRoute();
      else if (state.decisions.some(item => !previous.decisions.some(old => old.id === item.id)) || state.leaders.some(item => { const old = previous.leaders.find(old => old.session_id === item.session_id); return item.unread > 0 && (old === undefined || (item.last_seq > old.last_seq && (item.unread > old.unread || item.read_seq > old.read_seq))); })) beep();
      render(); syncRoute();
      if (view === "usage" && usage === null && !usageLoading) loadUsage();
    } catch (error) { toast(error.message); if (state === null) $("main").innerHTML = `<div class="workspace-home"><p class="error-line">${esc(error.message)}</p></div>`; }
    finally { pollBusy = false; complete(); }
  }
  async function answer(id, text) {
    const item = state.decisions.find(item => item.id === id);
    const result = await api("/api/answer", {id, text});
    decisionsOpen.delete(id); toast(`已答复 ${item.asker}，${result.routed_to === null ? "它下一轮会收到" : `${result.routed_to} 会叫醒它`}`); await loadState(true);
  }
  async function changeSession(path, label, undoable) {
    const id = cardId, result = await api(path, {session_id:id});
    closeCard(); await loadState(true); toast(label, undoable ? async () => { await api("/api/session/undo", {session_id:id, before:result.before}); await loadState(true); toast("已撤销"); } : null);
  }
  function setup() {
    $("overlay-root").innerHTML = '<button class="rp-scrim" id="rp-scrim" data-act="right-close" aria-label="关闭架构图"></button><div class="card-back" id="card-back"></div><section class="card" id="card" role="dialog" aria-modal="true" aria-label="下属卡片" tabindex="-1"></section><div class="menu" id="menu" role="menu"></div><div class="backdrop" id="backdrop"></div>';
    try { const stored = localStorage.getItem("crew.theme"); if (["system", "light", "dark"].includes(stored)) theme = stored; } catch (error) { toast(error.message); }
    applyTheme();
    document.addEventListener("pointerdown", unlockAudio, {once:true}); document.addEventListener("keydown", unlockAudio, {once:true});
    document.addEventListener("click", event => {
      if (event.target.id === "backdrop") { closeBackdrop(); return; }
      if (event.target.id === "card-back") { closeCard(); return; }
      const button = event.target.closest("[data-act]");
      if (!event.target.closest("#menu") && !event.target.closest('[data-act$="-menu"]')) hideMenu();
      if (button === null || button.disabled) return;
      const act = button.dataset.act, id = button.dataset.id;
      if (act === "leader") { if (cardId !== null) closeCard(); navigate("chat", id); }
      else if (act === "view") { if (cardId !== null) closeCard(); navigate(button.dataset.view); }
      else if (act === "all-tab") { allTab = button.dataset.tab; renderAll(); }
      else if (act === "card") openCard(id);
      else if (act === "card-close") closeCard();
      else if (act === "tray") { trayCollapsed = !trayCollapsed; renderChat(); }
      else if (act === "decision-toggle") { const key = Number(id); if (decisionsOpen.has(key)) decisionsOpen.delete(key); else decisionsOpen.add(key); if (view === "chat") renderChat(); else renderAll(); }
      else if (act === "answer-option") action(`answer:${id}`, () => answer(Number(id), state.decisions.find(item => item.id === Number(id)).options[Number(button.dataset.option)]));
      else if (act === "away") action("away", async () => { await api("/api/notifications/away", {away:!state.notification.manual_away}); await loadState(true); });
      else if (act === "theme") { theme = {system:"light", light:"dark", dark:"system"}[theme]; applyTheme(); try { localStorage.setItem("crew.theme", theme); } catch (error) { toast(error.message); } renderSidebar(); }
      else if (act === "right-open") { rightOpen = true; renderRight(); $("right-panel").querySelector('[data-act="right-close"]').focus(); }
      else if (act === "right-close") { rightOpen = false; renderRight(); $("main").querySelector('[data-act="right-open"]')?.focus(); }
      else if (act === "modal-leader") openModalLeader();
      else if (act === "modal-close") closeBackdrop();
      else if (act === "palette") openPalette();
      else if (act === "palette-go") { const item = paletteItems[Number(button.dataset.index)]; closeBackdrop(); item.go(); }
      else if (act === "others") { othersOpen = !othersOpen; renderAdopt(); }
      else if (act === "adopt-open") { if (adoptForms.has(id)) adoptForms.delete(id); else { const item = Object.values(state.candidates).flat().find(item => item.session_id === id); adoptForms.set(id, {name:item.crew_name === null ? item.title.slice(0, 6) : item.crew_name, group:state.leaders.length === 0 ? null : state.leaders[0].name}); } renderAdopt(); }
      else if (act === "adopt-leader") { adoptForms.get(id).group = button.dataset.group; renderAdopt(); }
      else if (act === "usage-period") { if (period === button.dataset.period) return; period = button.dataset.period; usage = null; renderUsage(); loadUsage(); }
      else if (act === "usage-tab") { usageTab = button.dataset.tab; renderUsage(); }
      else if (act === "usage-fold") { if (usageCollapsed.has(id)) usageCollapsed.delete(id); else usageCollapsed.add(id); renderUsage(); }
      else if (act === "rename") { rename = true; renderCard(); $("rename-form").querySelector("input").focus(); }
      else if (act === "rename-cancel") { rename = false; renderCard(); }
      else if (act === "move-menu") { const item = member(cardId); showMenu(button, state.leaders.filter(group => group.session_id !== item.leader_session_id).map(group => `<button data-act="move-to" data-group="${esc(group.name)}">${av(group.name, 20)}${esc(group.name)}</button>`).join("")); }
      else if (act === "more-menu") showMenu(button, `<button data-act="leave-group">${ic("leave", "sm")}退组</button><button class="danger" data-act="remove-session">${ic("x", "sm")}移出 Crew</button>`);
      else if (act === "move-to") action("move", async () => { const item = member(cardId); await api("/api/roster/move", {session_id:item.session_id, name:item.name, from:item.group, to:button.dataset.group}); hideMenu(); await loadState(true); toast(`已换到 ${button.dataset.group} 组`); });
      else if (act === "leave-group") action("leave", async () => { const item = member(cardId); await api("/api/roster/remove", {session_id:item.session_id, name:item.name, group:item.group}); hideMenu(); closeCard(); await loadState(true); toast("已退组，可以在待收编里重新收编"); });
      else if (act === "archive") action("archive", () => changeSession("/api/session/archive", "已归档", true));
      else if (act === "remove-session") action("remove", () => changeSession("/api/session/remove", "已移出 Crew", true));
      else if (act === "stop") action("stop", async () => { await api("/api/session/stop", {session_id:cardId}); await loadState(true); toast("已停下这一轮"); });
      else if (act === "open-vscode") action("open", async () => { const id = cardId, result = await api("/api/open", {session_id:id}); if (cardId === id) { handoff = result.needs_window_ready ? result : null; renderCard(); if (!result.needs_window_ready) toast("已在 VS Code 打开"); } });
      else if (act === "handoff-cancel") { handoff = null; renderCard(); }
      else if (act === "handoff-go") action("continue", async () => { await api("/api/open/continue", {session_id:cardId, confirm:handoff.needs_confirmation}); handoff = null; await loadState(true); toast("已在 VS Code 接上同一个会话"); });
      else if (act === "member-history") { const item = member(cardId); closeCard(); navigate("chat", item.leader_session_id, item.session_id); }
    });
    document.addEventListener("input", event => { const form = event.target.closest("[data-adopt-form]"); if (form && event.target.name === "name") adoptForms.get(form.dataset.adoptForm).name = event.target.value; });
    document.addEventListener("submit", event => {
      const form = event.target;
      if (form.dataset.answerForm !== undefined) { event.preventDefault(); const text = form.elements.answer.value.trim(); if (text !== "") action(`answer:${form.dataset.answerForm}`, () => answer(Number(form.dataset.answerForm), text)); }
      else if (form.dataset.adoptForm !== undefined) { event.preventDefault(); const id = form.dataset.adoptForm; action(`adopt:${id}`, async () => { const group = adoptForms.get(id).group, bucket = Object.keys(state.candidates).find(bucket => state.candidates[bucket].some(item => item.session_id === id)), item = state.candidates[bucket].find(item => item.session_id === id); await api("/api/roster/adopt", {session_id:id, name:form.elements.name.value.trim(), group}); adopted.set(id, {item, bucket, group}); adoptForms.delete(id); await loadState(true); }); }
      else if (form.id === "rename-form") { event.preventDefault(); action("rename", async () => { await api("/api/session/rename", {session_id:cardId, name:form.elements.name.value.trim()}); rename = false; await loadState(true); toast("已改名"); }); }
      else if (form.id === "leader-form") { event.preventDefault(); action("leader", async () => { const button = form.querySelector('[type="submit"]'); button.disabled = true; try { const result = await api("/api/leader", {name:form.elements.name.value.trim(), task:form.elements.task.value.trim()}); closeBackdrop(); await loadState(true); navigate("chat", result.session_id); } catch (error) { $("leader-error").textContent = error.message; } finally { button.disabled = false; } }); }
    });
    document.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && state !== null && project !== null) { event.preventDefault(); openPalette(); return; }
      if (event.key === "Escape") { if ($("menu").classList.contains("show")) { hideMenu(); return; } if ($("backdrop").classList.contains("show")) closeBackdrop(); else if (cardId !== null) closeCard(); else if (rightOpen) { rightOpen = false; renderRight(); } }
      if (event.key === "Tab") {
        const surface = $("backdrop").classList.contains("show") ? $("backdrop") : cardId !== null ? $("card") : null;
        if (surface === null) return;
        const items = Array.from(surface.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href]')).filter(item => item.getClientRects().length > 0), first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    window.addEventListener("hashchange", () => { if (state === null || project === null) return; CrewChat.unmount(); mountedChat = null; readRoute(); render(); if (view === "usage" && usage === null && !usageLoading) loadUsage(); });
    loadState(); setInterval(loadState, 3000);
  }
  setup();
})();
