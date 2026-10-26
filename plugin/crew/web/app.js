"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const projectDisplay = item => item.display_name || item.name;
  const project = new URLSearchParams(location.search).get("project");
  let state = null, view = "all", leaderId = null, readOnlyId = null, mountedChat = null, pollBusy = false, statePromise = Promise.resolve();
  let mountedChannel = false;
  let settingsUI = null, settingsOpen = false, settingsReturn = null;
  let workspaceSettings = null, workspaceOpen = false, maintenance = null, gitSettings = null;
  let nativeApprovals = null, memberUI = null, adoptLeaderId = null, sessionLibrary = null;
  let stopConfirmation = null;
  let documents = null, leaderTrust = null, runtimeSwitch = null, goals = null, goalId = null;
  let leaderDrag = null, leaderOrderSaving = false;
  let allTab = "tree", period = "week", usageTab = "tree", usage = null, usageLoading = false, usageRequest = 0;
  let cardId = null, cardReturn = null, handoff = null, rightOpen = false, dialogReturn = null;
  let theme = "system", paletteItems = [], paletteIndex = 0;
  let soundGeneration = 0;
  let runtimesCache = null, runtimesPromise = null, runtimesLoading = false, runtimesError = null;
  let commentsSession = null, auxiliaryDialog = 0;
  const commentsCache = new Map();
  const compactions = new Map();
  const waitingSounds = new Map();
  let fontScale = 1;
  const FONT_MIN = .9, FONT_MAX = 1.4;
  const decisionsOpen = new Set(), heldExpanded = new Set(), usageCollapsed = new Set(), operationIds = new Set();
  const todoExpanded = new Set(), todoActions = new Map(), todoSaving = new Set();
  let attentionScope = null;
  let composingInput = null, compositionTimer = null, compositionRenderPending = false;
  const colors = ["#3E7C74", "#8A5A7E", "#A07A3A", "#4F6B8F", "#5E7F55", "#A8604F", "#8C7650", "#5C5F9E", "#A35D6A", "#4F7A8C", "#77773F"];
  const statusText = {working:"在干活", waiting:"等你", idle:"空闲", offline:"离线", closed:"已归档"};
  const paths = {
    search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', plus:'<path d="M12 5v14M5 12h14"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    inbox:'<path d="m4 4-2 10v6h20v-6L20 4H4Z"/><path d="M2 14h6l2 3h4l2-3h6"/>', chart:'<path d="M4 3v18h17M9 16v-5m5 5V7m5 9V3"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon:'<path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/>', auto:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M12 4v13"/>',
    settings:'<path d="M9.5 3h5l.5 2.2 1.5.9 2.2-.7 2.5 4.3-1.7 1.5V13l1.7 1.5-2.5 4.3-2.2-.7-1.5.9-.5 2.2h-5L9 19l-1.5-.9-2.2.7-2.5-4.3L4.5 13v-1.8L2.8 9.7l2.5-4.3 2.2.7L9 5.2 9.5 3Z"/><circle cx="12" cy="12" r="3"/>',
    more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>', x:'<path d="m6 6 12 12M6 18 18 6"/>',
    right:'<path d="m9 5 7 7-7 7"/>', down:'<path d="m5 9 7 7 7-7"/>', check:'<path d="m5 12 4 4L19 6"/>',
    code:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14"/>', cloud:'<path d="M6 18a4 4 0 0 1-1-7.8A7 7 0 0 1 18 8a5 5 0 0 1 0 10H6Z"/>',
    bolt:'<path d="m13 2-9 12h7l-1 8 10-13h-7l1-7Z"/>', pencil:'<path d="m16 3 5 5L8 21H3v-5L16 3Zm-3 3 5 5"/>',
    swap:'<path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/>', archive:'<path d="M4 8v13h16V8M3 3h18v5H3V3Zm6 9h6"/>',
    stop:'<rect x="5" y="5" width="14" height="14" rx="2"/>', pause:'<path d="M8 5v14M16 5v14"/>', leave:'<path d="M9 3H3v18h6m6-14 5 5-5 5M8 12h12"/>',
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
  const tokens = value => value === null ? "未知" : value >= 1000000 ? `${count(value / 1000000)}M` : value >= 1000 ? `${count(value / 1000)}K` : count(value);
  const money = value => value === null ? "未定价" : `$${Number(value).toFixed(2)}`;
  const time = at => new Date(at).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false});
  const date = at => new Date(at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  function ago(at) { const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000); return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`; }
  const leader = id => state.leaders.find(item => item.session_id === id);
  const member = id => state.members.find(item => item.session_id === id);
  const workingPeople = people => people.filter(item => item.status === "working");
  const workingDispatches = people => people.flatMap(person => person.codex.filter(task => task.source === "skill_dispatch" && task.status === "running").map(task => ({person, task})));
  const HAIKU = "claude-haiku-4-5-20251001";
  const runtimeId = item => typeof item === "string" ? item : item?.runtime || "claude";
  const editorHandoffReason = item => runtimeId(item) === "codex" ? "Codex 会话在 Crew 内继续；暂不支持转到 VS Code" : "";
  function runtimeList() {
    return runtimesCache || (Array.isArray(state?.runtimes) ? state.runtimes : [{id:"claude", name:"Claude Code", available:true, error:null, models:state?.models || [], efforts:state?.efforts || []}]);
  }
  function runtimeInfo(item) {
    const id = runtimeId(item);
    return runtimeList().find(runtime => runtime.id === id) || {id, name:id === "codex" ? "Codex" : id, available:false, error:"这个执行工具还不可用，请重新检测。", models:[], efforts:[]};
  }
  function defaultRuntime(catalog = runtimeList()) {
    if (state?.ai?.default?.runtime) return state.ai.default.runtime;
    const available = catalog.filter(item => item.available);
    return available.length === 1 ? available[0].id : "";
  }
  function taskRuntime(purpose) { const configured = state?.ai?.[purpose]; return configured === null || configured === undefined ? defaultRuntime() : configured.runtime || defaultRuntimeForUnconfigured(); }
  function defaultRuntimeForUnconfigured() { const available = runtimeList().filter(item => item.available); return available.length === 1 ? available[0].id : ""; }
  const runtimePrompt = () => runtimeList().some(item => item.available) ? "请在设置中选择默认载体，或在这里选择本次使用的工具。" : noRuntime;
  const noRuntime = "没有可用的执行工具，请安装并登录 Codex 或 Claude Code，再重新检测。";
  const runtimeBadge = (item, compact = false) => `<span class="runtime-tag ${compact ? "compact" : ""}" data-runtime="${esc(runtimeId(item))}" title="执行工具：${esc(runtimeInfo(item).name)}">${esc(compact && runtimeId(item) === "claude" ? "CC" : runtimeInfo(item).name)}</span>`;
  function modelName(id, item = "claude") { const model = (runtimeInfo(item).models || []).find(model => model.id === id); return model === undefined ? id : model.name; }
  function modelEfforts(item, modelId) {
    if (runtimeId(item) === "claude" && modelId === HAIKU) return [];
    const runtime = runtimeInfo(item), model = (runtime.models || []).find(model => model.id === modelId);
    return model && Array.isArray(model.efforts) ? model.efforts : runtime.efforts || [];
  }
  const currentOptions = item => item.applied == null ? item : item.applied;
  function optionsLabel(item) { const options = currentOptions(item), model = options.model == null ? "默认" : modelName(options.model, item); return modelEfforts(item, options.model).length === 0 ? model : `${model} · ${options.effort == null ? "默认" : options.effort}`; }
  function optionChange(item, field, value) {
    const runtime = runtimeInfo(item), options = currentOptions(item);
    if (!runtime.available) throw new Error(runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。`);
    const changes = {[field]:value};
    if (field === "model") {
      if (value !== null && !(runtime.models || []).some(model => model.id === value)) throw new Error("模型列表已更新，请重新选择。");
      const efforts = modelEfforts(item, value), oldEffort = item.effort ?? options.effort;
      if (runtime.id === "codex" && oldEffort != null && !efforts.includes(oldEffort)) {
        if (item.where === "bg" || item.running) throw new Error("这个模型不支持当前 effort，请先选择兼容的 effort，或停下会话后切换。");
        changes.effort = null;
      }
    } else if (!modelEfforts(item, item.model ?? options.model).includes(value)) throw new Error("这个模型不支持所选 effort，请重新选择。");
    return changes;
  }
  async function refreshRuntimes() {
    // Older servers have only the Claude catalog. Never probe on the state poll.
    if (!Array.isArray(state?.runtimes)) return;
    if (runtimesPromise !== null) return runtimesPromise;
    runtimesLoading = true; runtimesError = null;
    renderLeaderRuntime();
    runtimesPromise = (async () => {
      try {
        const result = await api("/api/runtimes");
        if (!Array.isArray(result.runtimes)) throw new Error("执行工具列表格式无效");
        runtimesCache = result.runtimes;
      } catch (error) { runtimesError = error.message; }
      finally { runtimesLoading = false; runtimesPromise = null; renderLeaderRuntime(); }
    })();
    return runtimesPromise;
  }
  const memberModel = item => item.where === "bg" ? `<span class="member-model">${esc(optionsLabel(item))}</span>` : "";
  const memberActivity = item => item.where === "bg" ? CrewActivity.markup(item, true) : "";
  const contextRing = item => CrewContext.ring(item.context, {id:item.session_id, name:item.name, busy:item.busy,
    external:item.where === "vscode", phase:compactions.get(item.session_id)?.phase || (item.activity?.phase === "compacting" ? "compacting" : null)});
  const compactBusy = item => item.busy || item.status === "working";
  function contextControls(item) {
    const ring = contextRing(item);
    return ring === "" ? "" : `<span class="context-controls">${ring}</span>`;
  }
  const sessionOpen = (id, act, label, selected = false) => `<button type="button" class="session-open" data-act="session-open" data-open-act="${act}" data-id="${esc(id)}" aria-label="${esc(label)}" ${selected ? 'aria-current="page"' : ""}></button>`;
  const membersOf = id => state.members.filter(item => item.leader_session_id === id);
  function groupWork(item) {
    const team = membersOf(item.session_id), members = workingPeople(team).length;
    const self = item.status === "working", count = members + Number(self);
    const dispatches = workingDispatches([item, ...team]).length;
    const people = count === 0 ? "" : self ? members > 0 ? `本组 ${count} 人在干活` : "本人在干活" : `${members} 位组员在干活`;
    const label = [people, dispatches > 0 ? `${dispatches} 个派工在运行` : ""].filter(Boolean).join(" · ");
    const summary = count === 0 && dispatches > 0 ? label : `本组 ${count} 人在干活${dispatches > 0 ? ` · 派工 ${dispatches}` : ""}`;
    return {self, members, count, dispatches, active:count + dispatches > 0, label, summary};
  }
  const groupAvatar = (item, work) => !work.active ? av(item.name, 30) : `<span class="group-work-avatar" title="${esc(work.label)}">${av(item.name, 30)}</span>`;
  const groupWorkLabel = (work, compact = false) => !work.active ? "" : `<span class="group-work-label" title="${esc(work.label)}"><i aria-hidden="true"></i>${esc(compact && work.dispatches === 0 && !work.self ? `组员 ${work.members} 在干活` : compact && work.dispatches === 0 && work.members === 0 ? "在干活" : work.label)}</span>`;
  const decisionsOf = id => state.decisions.filter(item => item.leader_session_id === id || item.session_id === id);
  const pendingOf = id => decisionsOf(id).filter(item => !item.held);
  const openTodos = () => (Array.isArray(state?.todos) ? state.todos : []).filter(item => item.status === undefined || item.status === "open");
  const todoById = id => openTodos().find(item => String(item.id) === String(id));
  const todosOf = id => openTodos().filter(item => item.leader_session_id === id || item.session_id === id);
  function queryPath(path) { const url = new URL(path, location.origin); if (project !== null && url.pathname !== "/api/settings") url.searchParams.set("project", project); return url.pathname + url.search; }
  async function api(path, data) {
    const sentAfter = path === "/api/chat/send" ? (leader(data.session_id) || member(data.session_id))?.last_seq || 0 : null;
    const response = await fetch(queryPath(path), data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Crew":"1"}, body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || "请求失败，请重试。"); error.status = response.status; error.details = result; throw error; }
    if (path === "/api/chat/send" && data.goal_id == null) waitingSounds.set(data.session_id, {generation:++soundGeneration, afterSeq:sentAfter});
    else if (["/api/session/stop", "/api/session/archive", "/api/session/remove"].includes(path)) waitingSounds.delete(data.session_id);
    return result;
  }
  function toast(message, undo = null) {
    const node = document.createElement("div"); node.className = "toast show"; node.setAttribute("role", "status"); node.textContent = message;
    if (undo !== null) { const button = document.createElement("button"); button.type = "button"; button.textContent = "撤销"; button.onclick = async () => { button.disabled = true; try { await undo(); node.remove(); } catch (error) { button.disabled = false; toast(error.message); } }; node.append(button); }
    $("toast-root").replaceChildren(node); setTimeout(() => node.remove(), undo === null ? 5000 : 12000);
  }
  function replace(node, html) {
    if (node.dataset.render === html) return;
    // Recreating a textarea commits/discards the native IME session even if its
    // value and caret are restored. Keep the composing DOM node connected.
    if (composingInput !== null && node.contains(composingInput)) { compositionRenderPending = true; return; }
    const focused = node.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset.preserve;
    const focusData = focused === null ? null : {...focused.dataset};
    const focusForm = focused?.form;
    const selection = focused && "selectionStart" in focused ? [focused.selectionStart, focused.selectionEnd] : null;
    const values = new Map(Array.from(node.querySelectorAll("[data-preserve]")).filter(item => "value" in item).map(item => [item.dataset.preserve, item.value]));
    const scroll = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-in,.lead-grid,.comments-list,.group-goal-list,.sb-leaders")).map(item => [item.className, item.scrollTop, item.scrollLeft]);
    node.innerHTML = html; node.dataset.render = html;
    for (const item of node.querySelectorAll("[data-preserve]")) if (values.has(item.dataset.preserve) && "value" in item) item.value = values.get(item.dataset.preserve);
    for (const [name, top, left] of scroll) { const item = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-in,.lead-grid,.comments-list,.group-goal-list,.sb-leaders")).find(item => item.className === name); if (item) { item.scrollTop = top; item.scrollLeft = left; } }
    if (focusKey !== undefined) { const item = Array.from(node.querySelectorAll("[data-preserve]")).find(item => item.dataset.preserve === focusKey); if (item) { item.focus({preventScroll:true}); if (selection) item.setSelectionRange(...selection); } }
    else if (focused !== null) {
      const item = Array.from(node.querySelectorAll(focused.tagName)).find(item => item.id === focused.id && item.getAttribute("name") === focused.getAttribute("name") && item.getAttribute("type") === focused.getAttribute("type") && Object.entries(focusData).every(([key, value]) => item.dataset[key] === value) && (focusForm === null || focusForm === undefined || item.form !== null && item.form.id === focusForm.id && Object.entries(focusForm.dataset).every(([key, value]) => item.form.dataset[key] === value)));
      if (item) item.focus({preventScroll:true});
    }
  }
  async function action(key, fn) { if (operationIds.has(key)) return; operationIds.add(key); try { await fn(); } catch (error) { toast(error.message); } finally { operationIds.delete(key); } }
  function quotas() { return [["Claude 每周", state.limits.claude.seven_day], ["Claude 5 小时", state.limits.claude.five_hour], ["Codex 每周", state.limits.codex]]; }
  // A window past its reset keeps used_percent null until the next observation.
  function quotaText(limit) { return limit === null ? "未采集" : limit.used_percent === null ? (limit.expired ? "已重置" : "未采集") : `${count(limit.used_percent)}%`; }
  const crewPercent = value => `${value < 1 ? "<1" : Math.round(value)}%`;
  function crewQuota(limit, withCost = false) {
    if (limit === null || limit.crew === undefined || limit.crew.points === null) return "";
    const crew = limit.crew, cost = `$${Math.round(crew.cost_usd).toLocaleString("zh-CN")}`;
    const title = `按每 1% ≈ $${crew.usd_per_point} 折算（社区实测 $${crew.usd_per_point_range[0]}～${crew.usd_per_point_range[1]}，对应 ${crewPercent(crew.low)}～${crewPercent(crew.high)}）。Crew = 进了组的 leader 和组员，含子 agent；本周 Crew 折合 ${cost}。`;
    return `<div class="quota-crew" title="${esc(title)}">其中 Crew ≈ ${esc(crewPercent(crew.points))}${withCost ? `（折合 ${cost}）` : ""}</div>`;
  }
  function applyTheme() { CrewTheme.setMode(theme); if (state !== null) CrewTheme.apply(state.themes, state.project === null ? null : state.project.theme, theme); }
  function applyFontScale() { document.documentElement.style.setProperty("--fs", String(fontScale)); }
  function setFontScale(value) { fontScale = value; applyFontScale(); try { localStorage.setItem("crew.fontScale", String(value)); } catch (error) { toast(error.message); } }
  function decisionTrust(item) {
    const owner = leader(item.leader_session_id) || leader(item.session_id);
    return window.CrewLeaderTrust?.trustFor(owner) || {enabled:false};
  }
  function trustReportUrl(id) {
    return queryPath(`/api/trust/report?leader_session_id=${encodeURIComponent(id)}`);
  }
  async function readTrustReport(id) {
    if (!leader(id)) throw new Error("请先选择当前项目的 leader。");
    const response = await fetch(trustReportUrl(id), {headers:{"X-Crew":"1"}});
    const text = await response.text();
    if (!response.ok) { let error; try { error = JSON.parse(text).error; } catch (_) {} throw new Error(error || "报告读取失败，请稍后重试。"); }
    return text;
  }
  function renderSettings() {
    const sound = CrewSound.getSettings(), pct = value => `${Math.round(value * 100)}%`;
    const themes = state !== null && project !== null ? `<section class="settings-section"><h4 id="project-theme-title">主题</h4><div class="theme-options" role="group" aria-labelledby="project-theme-title">${state.themes.map(item => `<button type="button" class="theme-option" data-act="project-theme" data-theme-id="${esc(item.id)}" aria-pressed="${state.project.theme === item.id}"><span class="theme-swatches" aria-hidden="true"><i style="background:${item.light["--accent"]}"></i><i style="background:${item.dark["--accent"]}"></i></span><span>${esc(item.name)}</span>${state.project.theme === item.id ? ic("check", "sm") : ""}</button>`).join("")}</div><p class="settings-hint">左浅右深 · 为这个项目保存</p></section>` : "";
    replace($("settings"), `<section class="settings-section"><h4>显示模式</h4><div class="cs-theme-options">${[["system","跟随系统"],["light","浅色"],["dark","深色"]].map(([id,label]) => `<button type="button" data-act="theme-select" data-theme-mode="${id}" aria-pressed="${theme === id}">${label}</button>`).join("")}</div></section>${themes}<section class="settings-section"><div class="settings-row"><h4>字号</h4><span class="settings-value" id="font-scale-value">${pct(fontScale)}</span><button type="button" class="settings-link" data-act="font-reset" ${fontScale === 1 ? "disabled" : ""}>恢复标准</button></div><input class="settings-range" type="range" min="${FONT_MIN}" max="${FONT_MAX}" step="0.01" value="${fontScale}" data-setting="font" aria-label="字号"></section><section class="settings-section"><div class="settings-row"><h4>提示音</h4><span class="gap"></span><button class="switch ${sound.muted ? "" : "on"}" data-act="sound-mute" role="switch" aria-checked="${!sound.muted}" aria-label="提示音"><span class="track"></span></button></div><div class="settings-row"><span class="settings-sub">音量</span><span class="settings-value" id="sound-volume-value">${pct(sound.volume)}</span><button type="button" class="settings-link" data-act="sound-test" ${sound.muted ? "disabled" : ""}>试听</button></div><input class="settings-range" type="range" min="0" max="1" step="0.01" value="${sound.volume}" data-setting="volume" aria-label="提示音音量" ${sound.muted ? "disabled" : ""}></section>`);
  }
  function notificationChannel() {
    const channels = state.notification.channels || [{...state.notification.channel, id:"qq", name:"QQ"}];
    const labels = {unconfigured:"未配置", disabled:"已断开", disconnected:"已断开", unbound:"等待绑定", connecting:"连接中", connected:"已连接", reconnecting:"重连中", rejected:"被拒收", error:"出错", stopped:"已停止"};
    return channels.map(channel => {
      const hint = channel.state === "unbound" ? "在设置的消息渠道中复制绑定命令，私聊机器人完成绑定" : channel.state === "rejected" && channel.id === "qq" ? "在手机 QQ 打开允许主动发送" : channel.detail;
      return `<div class="qq-status ${esc(channel.state)}" role="status"${hint ? ` title="${esc(hint)}"` : ""}><div class="qq-state"><i></i>${esc(channel.name)} · ${esc(labels[channel.state] || channel.state)}</div>${hint ? `<div class="qq-detail">${esc(hint)}</div>` : ""}</div>`;
    }).join("");
  }
  const priorityLeaders = () => state.leaders.filter(item => item.priority !== null).sort((a, b) => a.priority - b.priority);
  const priorityIds = () => priorityLeaders().map(item => item.session_id);
  const sortableLeaders = item => item.priority === null ? state.leaders.filter(leader => leader.priority === null) : priorityLeaders();
  const priorityAttrs = item => item.priority === null ? "" : `data-level="${item.priority}"`;
  const priorityBadge = (rank, corner = false) => rank === null ? "" : `<span class="priority-mark ${corner ? "corner" : ""}" data-level="${rank}" title="第 ${rank} 优先"><span class="rank">${rank}</span></span>`;
  function decisionPriority(item) {
    const owner = leader(item.leader_session_id);
    return owner === undefined ? null : owner.priority;
  }
  async function savePriority(ranks, before) {
    const evicted = before.find(id => !ranks.includes(id));
    const inserted = ranks.some(id => !before.includes(id));
    const evictedName = evicted === undefined ? null : leader(evicted).name;
    leaderOrderSaving = true;
    try {
      await api("/api/leaders/priority", {project:state.project.name, ranks});
      if (inserted && evicted !== undefined) toast(`${evictedName} 已移出优先`, async () => {
        await api("/api/leaders/priority", {project:state.project.name, ranks:before});
        await loadState(true); toast("已撤销");
      });
    } finally { leaderOrderSaving = false; await loadState(true); }
  }
  async function saveNormalOrder(ids) {
    const normal = ids.slice(), order = state.leaders.map(item => item.priority === null ? normal.shift() : item.session_id);
    leaderOrderSaving = true;
    try { await api("/api/leaders/order", {project:state.project.name, order}); }
    finally { leaderOrderSaving = false; await loadState(true); }
  }
  function setPriority(id, rank) {
    const before = priorityIds(), ranks = before.filter(sid => sid !== id);
    if (rank !== 0) ranks.splice(rank - 1, 0, id);
    return savePriority(ranks.slice(0, 3), before);
  }
  function sidebarLeader(item) {
    const selected = view === "chat" && leaderId === item.session_id && readOnlyId === null;
    const approvals = nativeApprovals?.countForLeader(item.session_id) || 0;
    const work = groupWork(item);
    const label = [item.name, item.priority === null ? "" : `第 ${item.priority} 优先`, work.label, item.unread > 0 ? `${item.unread} 条未读` : "", item.pending > 0 ? `${item.pending} 件待拍板` : "", approvals ? `${approvals} 项执行权限待确认` : ""].filter(Boolean).join("，");
    return `<div draggable="true" class="leader-item session-surface ${item.priority === null ? "" : "pinned"} ${selected ? "active" : ""}" data-act="leader" data-id="${esc(item.session_id)}" title="${esc(label)}" aria-label="${esc(label)}" ${selected ? 'aria-current="page"' : ""}>${sessionOpen(item.session_id, "leader", label, selected)}${groupAvatar(item, work)}<span class="meta"><span class="nm"><span class="leader-name">${esc(item.name)}</span>${item.priority === null ? "" : `<span class="leader-priority" title="第 ${item.priority} 优先">优先 ${item.priority}</span>`}</span><span class="goal">${CrewChat.renderInline(item.goal, item.session_id)}</span></span><span class="leader-details">${runtimeBadge(item, true)}${item.is_git_admin ? '<span class="tag git-admin-tag">Git</span>' : ""}${contextRing(item)}${groupWorkLabel(work, true)}<span class="badges">${approvals ? `<span class="native-approval-count" title="${approvals} 项执行权限待确认">权限 ${approvals}</span>` : ""}${item.pending > 0 ? `<span class="b-pending" title="${item.pending} 件等你拍板">${item.pending}</span>` : ""}${item.unread > 0 ? `<span class="b-unread" title="${item.unread} 条未读">${item.unread}</span>` : ""}</span></span></div>`;
  }
  function renderSidebar() {
    if (leaderDrag !== null || leaderOrderSaving) return;
    const themeIcon = {system:"auto", light:"sun", dark:"moon"}[theme], themeName = {system:"跟随系统", light:"浅色", dark:"深色"}[theme];
    const quotaTitle = quotas().map(([label, limit]) => `${label} ${quotaText(limit)}`).join("\n");
    replace($("sidebar"), `<div class="brand"><span class="brand-logo" aria-hidden="true">${CrewTheme.logo()}</span><div class="brand-copy"><div class="proj" title="${esc(projectDisplay(state.project))}">${esc(projectDisplay(state.project))}</div><div class="sub">Crew 工作区</div></div><button class="icon-btn workspace-settings-entry" data-act="workspace-settings" title="当前工作区设置" aria-label="当前工作区设置">${ic("settings", "sm")}</button></div>
      <button class="search-btn" data-act="palette" title="找 leader 或下属 · Ctrl K">${ic("search", "sm")}<span class="search-label">找 leader 或下属</span><span class="kbd">Ctrl K</span></button>
      <button class="nav-item ${view === "all" ? "active" : ""}" data-act="view" data-view="all">${ic("grid")}<span>全部</span>${nativeApprovals?.count() ? `<span class="native-approval-count" title="执行权限待确认">权限 ${nativeApprovals.count()}</span>` : ""}</button>
      <button class="nav-item ${view === "channel" ? "active" : ""}" data-act="view" data-view="channel">${ic("msg")}<span>交流区</span>${state.channel.unread > 0 ? `<span class="b-unread channel-unread" title="交流区未读">${state.channel.unread}</span>` : ""}</button>
      <button class="nav-item ${view === "git" ? "active" : ""}" data-act="view" data-view="git">${ic("branch")}<span>Git</span></button>
      <button class="nav-item ${view === "todos" ? "active" : ""}" data-act="view" data-view="todos">${ic("check")}<span>我的待办</span>${openTodos().length > 0 ? `<span class="todo-count">${openTodos().length}</span>` : ""}</button>
      <div class="sb-leaders"><section class="priority-zone ${priorityIds().length === 0 ? "empty-priority" : ""}" data-leader-zone="priority"><div class="sec-label">置顶 · 优先</div>${priorityLeaders().map(sidebarLeader).join("")}<div class="priority-drop-hint">拖到这里设为优先</div></section>
      <section class="leader-list" data-leader-zone="normal"><div class="sec-label">Leader</div>${state.leaders.filter(item => item.priority === null).map(sidebarLeader).join("")}
      <button class="leader-item add-leader" data-act="modal-leader"><span class="plus">${ic("plus", "sm")}</span><span>起 leader</span></button></section></div>
      <div class="sb-mid"><button class="nav-item ${view === "adopt" ? "active" : ""}" data-act="view" data-view="adopt">${ic("inbox")}<span>会话库</span></button><button class="nav-item ${view === "usage" ? "active" : ""}" data-act="view" data-view="usage">${ic("chart")}<span>用量</span></button></div>
      <div class="sb-foot"><button class="quota quota-summary" data-act="view" data-view="usage" title="${esc(quotaTitle)}" aria-label="查看完整额度和用量"><span class="quota-summary-row"><span>Claude</span><span>周 ${quotaText(state.limits.claude.seven_day)} · 5h ${quotaText(state.limits.claude.five_hour)}</span></span><span class="quota-summary-row"><span>Codex</span><span>周 ${quotaText(state.limits.codex)}</span></span></button>${notificationChannel()}<div class="foot-row"><button class="switch ${state.notification.manual_away ? "on" : ""}" data-act="away" role="switch" aria-checked="${state.notification.manual_away}"><span class="track"></span><span>我已离开</span></button><span class="gap"></span><button class="icon-btn" data-act="settings" title="设置" aria-label="设置">${ic("settings")}</button><button class="icon-btn" data-act="theme" title="${themeName}" aria-label="主题：${themeName}">${ic(themeIcon)}</button></div>${state.notification.away ? '<div class="away-note">离开中：待拍板会发到已连接的消息渠道</div>' : ""}${state.notification.error === null ? "" : `<div class="error-line">${esc(state.notification.error)}</div>`}</div>`);
  }
  function clearLeaderDrop() {
    $("sidebar").querySelectorAll(".drop-before,.drop-after,.drop-zone").forEach(item => item.classList.remove("drop-before", "drop-after", "drop-zone"));
  }
  function finishLeaderDrag() {
    leaderDrag.classList.remove("dragging");
    leaderDrag = null;
    $("sidebar").classList.remove("leader-drag-active");
    clearLeaderDrop();
  }
  document.addEventListener("dragstart", event => {
    const item = event.target.closest('#sidebar [data-act="leader"]');
    if (item === null) return;
    if (leaderOrderSaving) { event.preventDefault(); return; }
    leaderDrag = item;
    item.classList.add("dragging");
    requestAnimationFrame(() => {
      if (leaderDrag !== null) $("sidebar").classList.add("leader-drag-active");
    });
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.dataset.id);
  });
  document.addEventListener("dragover", event => {
    if (leaderDrag === null) return;
    clearLeaderDrop();
    const zone = event.target.closest('#sidebar [data-leader-zone]');
    if (zone === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    zone.classList.add("drop-zone");
    const item = event.target.closest('[data-act="leader"]');
    if (item !== null && item !== leaderDrag) item.classList.add(event.clientY < item.getBoundingClientRect().top + item.offsetHeight / 2 ? "drop-before" : "drop-after");
  });
  document.addEventListener("drop", event => {
    if (leaderDrag === null) return;
    const zone = event.target.closest('#sidebar [data-leader-zone]');
    if (zone === null) return;
    event.preventDefault();
    const id = leaderDrag.dataset.id, source = leaderDrag.closest('[data-leader-zone]').dataset.leaderZone;
    const target = zone.dataset.leaderZone, item = event.target.closest('[data-act="leader"]');
    if (item === leaderDrag) { finishLeaderDrag(); renderSidebar(); return; }
    const before = priorityIds(), after = item !== null && item.classList.contains("drop-after");
    const ids = [...zone.querySelectorAll('[data-act="leader"]')].map(item => item.dataset.id).filter(sid => sid !== id);
    const index = item === null ? ids.length : ids.indexOf(item.dataset.id) + (after ? 1 : 0);
    ids.splice(index, 0, id);
    finishLeaderDrag();
    if (target === "priority") action("leader-order", () => savePriority(ids.slice(0, 3), before));
    else if (source === "priority") action("leader-order", () => savePriority(before.filter(sid => sid !== id), before));
    else action("leader-order", () => saveNormalOrder(ids));
  });
  document.addEventListener("dragend", () => {
    if (leaderDrag === null) return;
    finishLeaderDrag();
    renderSidebar();
  });
  function compactDecisionText(value, limit = 88) {
    const firstLine = String(value || "").split(/\r?\n/).find(line => line.trim() !== "") || "待你拍板";
    const text = firstLine.trim().replace(/\s+/g, " "), characters = Array.from(text);
    return characters.length > limit ? characters.slice(0, limit).join("") + "…" : text;
  }
  function decisionOptions(item) {
    const brief = item.brief && typeof item.brief === "object" ? item.brief : null;
    return (item.options || []).map((original, index) => {
      const option = brief?.options?.[index], key = String.fromCharCode(65 + index);
      return {label:option?.label || compactDecisionText(String(original).replace(/^[A-Z][.、．]\s*/, ""), 80),
        consequence:option?.consequence || null, index,
        recommended:item.recommendation != null && item.recommendation !== "" && (item.recommendation === key || item.recommendation === original || item.recommendation === option?.label)};
    });
  }
  function decisionPreview(value, sessionId, limit = 280) {
    const text = String(value || "").replace(/\s+/g, " "), characters = Array.from(text);
    return CrewChat.renderInline(characters.length > limit ? characters.slice(0, limit).join("") + "…" : text, sessionId);
  }
  function decisionLongExplanation(item, options) {
    const brief = item.brief;
    return `<section class="decision-full-brief"><h4>为什么需要你</h4>${CrewChat.renderMarkdown(brief.why_user || "", item.session_id)}<h4>推荐理由</h4>${CrewChat.renderMarkdown(brief.recommendation_reason || "", item.session_id)}${brief.impact ? `<h4>影响</h4>${CrewChat.renderMarkdown(brief.impact, item.session_id)}` : ""}<h4>完整选项和后果</h4>${options.map(option => `<h5>${CrewChat.renderInline(option.label, item.session_id)}</h5>${option.consequence ? CrewChat.renderMarkdown(option.consequence, item.session_id) : '<p class="note">尚未说明结果</p>'}`).join("")}</section>`;
  }
  function decisionCard(item) {
    const stage = item.presentation_stage || "notice";
    if (stage !== "ready") return `<article class="dc decision-notice" data-dc="${item.id}"><div>${av(item.asker, 22, true)}<span>${esc(item.asker)}：这里需要你${stage === "discussion" ? "，先聊清背景和影响" : ""}</span></div><button class="btn" data-act="decision-discuss" data-id="${item.id}">${stage === "discussion" ? "继续聊" : "听它说明"}</button>${stage === "discussion" ? `<button class="btn ghost" data-act="decision-ready" data-id="${item.id}">聊清楚了，给我选项</button>` : ""}</article>`;
    const opened = decisionsOpen.has(item.id), brief = item.brief && typeof item.brief === "object" ? item.brief : null;
    const options = decisionOptions(item), title = brief?.title || compactDecisionText(item.question);
    const recommended = options.find(option => option.recommended);
    const missing = !brief || !brief.why_user || options.some(option => !option.consequence) || (item.recommendation && !brief.recommendation_reason);
    const context = brief ? brief.context : item.question;
    const longBrief = brief && ([brief.why_user, brief.recommendation_reason, brief.impact, ...options.flatMap(option => [option.label, option.consequence])].some(value => Array.from(String(value || "")).length > 280) || options.some(option => Array.from(option.label).length > 140));
    return `<article class="dc decision-card ${item.held ? "held" : ""}" data-dc="${item.id}"><div class="dc-head"><div class="dc-top">${av(item.asker, 22, true)}<h3 class="q">${CrewChat.renderInline(title, item.session_id)}</h3>${priorityBadge(decisionPriority(item))}<span class="from">${esc(item.asker)}</span></div><button class="btn ghost dc-hold" data-act="${item.held ? "decision-unhold" : "decision-hold"}" data-id="${item.id}">${item.held ? "取消挂起" : "挂起"}</button></div><div class="decision-summary">${brief?.why_user ? `<p class="decision-why"><span>为什么需要你</span>${decisionPreview(brief.why_user, item.session_id)}</p>` : '<p class="decision-missing">缺少说明：还没说清为什么需要你来决定。</p>'}${brief?.recommendation_reason ? `<div class="decision-recommendation"><strong>${recommended ? `建议：${decisionPreview(recommended.label, item.session_id, 140)}` : "推荐理由"}</strong><p>${decisionPreview(brief.recommendation_reason, item.session_id)}</p></div>` : ""}${brief?.impact ? `<p class="decision-impact"><span>影响</span>${decisionPreview(brief.impact, item.session_id)}</p>` : ""}${missing ? `<div class="decision-clarify"><span>这张卡的说明还不完整</span><button type="button" class="text-link" data-act="decision-clarify" data-id="${item.id}">让 leader 说明白</button></div>` : ""}</div><div class="dc-body"><div class="opts">${options.map(option => `<button class="opt ${option.recommended ? "recommended" : ""}" data-act="answer-option" data-id="${item.id}" data-option="${option.index}"><span class="t"><strong>${decisionPreview(option.label, item.session_id, 140)}</strong><small class="${option.consequence ? "" : "missing-consequence"}">${option.consequence ? decisionPreview(option.consequence, item.session_id) : "尚未说明这个选择的结果"}</small></span>${option.recommended ? '<span class="rec">推荐</span>' : ""}</button>`).join("")}</div><form class="free" data-answer-form="${item.id}"><input data-preserve="answer-${item.id}" name="answer" placeholder="或者直接说你的想法，回车发送" required aria-label="答复 ${esc(item.asker)}"><button class="icon-btn" type="submit" title="发送答复">${ic("check", "sm")}</button></form>${context || !brief || longBrief ? `<details class="decision-context" data-decision-context="${item.id}" ${opened ? "open" : ""}><summary>${brief ? longBrief ? "完整说明与技术背景" : "技术背景" : "查看原文和原选项"}</summary><div class="markdown">${longBrief ? decisionLongExplanation(item, options) : ""}${context ? CrewChat.renderMarkdown(context, item.session_id) : ""}${brief ? "" : `<ul>${(item.options || []).map(option => `<li>${CrewChat.renderInline(option, item.session_id)}</li>`).join("")}</ul>`}</div></details>` : ""}${decisionTrust(item).enabled ? '<p class="decision-trust-note">信任模式已开：已授权范围内由 AI 自主推进，需要亲自动手的事见“我的待办”。</p>' : ""}</div></article>`;
  }
  function heldGroup(items, context) {
    if (items.length === 0) return "";
    const expanded = heldExpanded.has(context);
    return `<section class="held-group ${context === "all" ? "ov-group" : ""} ${expanded ? "" : "collapsed"}" data-holds="${context}"><button class="tray-head held-head" data-act="holds" data-context="${context}" aria-expanded="${expanded}" aria-controls="held-list-${context}">${ic("down", "sm chev")}挂起 · ${items.length}</button><div class="${context === "tray" ? "tray-list" : "ov-list"} held-list" id="held-list-${context}">${items.map(decisionCard).join("")}</div></section>`;
  }
  function codexMarkup(item, cls = "cx", sessionId) {
    const running = item.status === "running";
    const label = ({running:"运行中",succeeded:"已完成",failed:"失败",interrupted:"已暂停",closed:"已关闭",unknown:"状态未确认"})[item.status] || "状态未确认";
    const icon = item.status === "succeeded" ? "check" : item.status === "failed" ? "x" : item.status === "interrupted" ? "pause" : item.status === "closed" ? "stop" : "more";
    const hint = `${item.runtime === "claude" ? "Claude" : "Codex"} ${item.source === "native_subagent" ? "子代理" : "派活"} · ${label}${item.pid ? " · PID " + item.pid : ""}${item.cwd ? " · " + item.cwd : ""}${item.detail ? " · " + item.detail : ""}`;
    return `<div class="${cls} codex-status-${esc(item.status)}" data-codex-source="${esc(item.source || "dispatch")}" title="${esc(hint)}">${ic("bolt", "xs")}${item.source === "skill_dispatch" ? runtimeBadge(item, true) : ""}<span class="${cls === "cx" ? "n" : "grow"}">${CrewChat.renderInline(item.task_name, sessionId)}</span><span class="s" aria-label="${esc(label)}">${running ? `<span class="spin" style="width:10px;height:10px"></span>${Math.max(0, Math.floor((Date.now() - new Date(item.started_at)) / 60000))} 分钟` : `${ic(icon, item.status === "succeeded" ? "xs ok" : "xs")}${item.status === "succeeded" && item.ended_at ? time(item.ended_at) : esc(label)}`}</span></div>`;
  }
  const codexList = item => item.codex.length > 0 || item.codex_older > 0 ? `<span class="cx-list">${item.codex.map(task => codexMarkup(task, "cx", item.session_id)).join("")}${item.codex_older > 0 ? `<span class="cx older">${ic("more", "xs")}<span class="n">更早 ${item.codex_older} 个</span></span>` : ""}</span>` : "";
  const commentButton = (item, group = false) => `<button type="button" class="session-comment" data-act="comments" data-id="${esc(item.session_id)}" aria-label="${esc(group ? item.name + "组的评论" : "评论" + item.name)}">${ic("msg", "xs")}${group ? "组评论" : "评论"}</button>`;
  function memberNode(item) {
    const where = item.where === "bg" ? "cloud" : item.where === "vscode" ? "code" : "clock";
    return `<div class="session-node"><div class="node session-surface click ${readOnlyId === item.session_id ? "sel" : ""}" data-act="member-chat" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}">${sessionOpen(item.session_id, "member-chat", `和「${item.name}」对话`)}<span class="node-top">${av(item.name, 26, true)}<span class="nm">${esc(item.name)}${runtimeBadge(item, true)}${contextRing(item)}<span class="where" title="${item.where === "bg" ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}">${ic(where, "xs")}</span></span>${st(item.status)}</span>${memberModel(item)}<span class="now">${item.task === null ? "还没写" : CrewChat.renderInline(item.task, item.session_id)}</span>${memberActivity(item)}${codexList(item)}</div><div class="session-node-actions">${commentButton(item)}<button class="session-comment" data-act="card" data-id="${esc(item.session_id)}">资料与操作</button></div></div>`;
  }
  function leaderNode(item, clickable) {
    const work = groupWork(item);
    return `<div class="session-node leader-unit"><${clickable ? "button" : "div"} class="node ${clickable ? "click" : ""} leader-node ${item.priority === null ? "" : "priority"}" ${priorityAttrs(item)} ${clickable ? `data-act="leader" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}"` : ""}>${priorityBadge(item.priority, true)}<span class="node-top">${groupAvatar(item, work)}<span class="nm">${esc(item.name)}<span class="tag" style="background:var(--hover);color:var(--text-3)">leader</span>${runtimeBadge(item)}</span>${item.pending > 0 ? `<span class="b-pending" style="margin-left:auto">${item.pending}</span>` : st(item.status)}</span><span class="leader-work-summary">${groupWorkLabel(work)}</span><span class="now" style="margin-left:39px">${CrewChat.renderInline(item.goal, item.session_id)}</span>${codexList(item)}</${clickable ? "button" : "div"}><div class="session-node-actions">${commentButton(item, true)}</div></div>`;
  }
  function workingRow(item) {
    const isLeader = leader(item.session_id) !== undefined;
    const task = isLeader ? item.goal : item.task;
    return `<div class="li click session-surface" data-act="${isLeader ? "leader" : "member-chat"}" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}">${sessionOpen(item.session_id, isLeader ? "leader" : "member-chat", `和「${item.name}」对话`)}${av(item.name, 32, !isLeader)}<span class="grow"><span class="ttl">${esc(item.name)}${runtimeBadge(item, true)}${contextRing(item)} <span class="where">${ic(item.where === "bg" ? "cloud" : "code", "xs")}</span><span class="li-sub">${isLeader ? "leader" : `归 ${esc(item.group)}`}</span>${memberModel(item)}</span><span class="li-now">${task === null ? "还没写" : CrewChat.renderInline(task, item.session_id)}</span>${memberActivity(item)}<span class="sm">${item.codex.filter(task => task.status === "running").map(task => `<span>${ic("bolt", "xs")} ${CrewChat.renderInline(task.task_name, item.session_id)} · <span class="spin" style="width:9px;height:9px;display:inline-block"></span> ${Math.floor((Date.now() - new Date(task.started_at)) / 60000)} 分钟</span>`).join("")}</span></span>${st(item.status)}</div>`;
  }
  function workingDispatchRow({person, task}) {
    const isLeader = leader(person.session_id) !== undefined;
    return `<button class="li click" data-act="${isLeader ? "leader" : "member-chat"}" data-id="${esc(person.session_id)}" data-dispatch-id="${esc(task.id)}" title="查看发起人对话 · PID ${esc(task.pid)}"><span class="av" style="--s:32px;--tint:var(--working)">${ic("bolt", "sm")}</span><span class="grow"><span class="ttl">${CrewChat.renderInline(task.task_name, person.session_id)}${runtimeBadge(task, true)}</span><span class="li-sub">临时派工 · 由 ${esc(person.name)} 派出</span></span><span class="li-now">PID ${esc(task.pid)}</span>${st("working")}</button>`;
  }
  function renderRight() {
    const chat = view === "chat" && leaderId !== null;
    $("right-panel").classList.toggle("show", chat && rightOpen);
    $("rp-scrim").classList.toggle("show", chat && rightOpen);
    const toggle = $("main").querySelector('[data-act="right-open"]');
    if (toggle) { toggle.setAttribute("aria-expanded", String(rightOpen)); toggle.classList.toggle("active", rightOpen); }
    if (view !== "chat" || leaderId === null) { $("right-panel").innerHTML = ""; delete $("right-panel").dataset.render; return; }
    const item = leader(leaderId), members = membersOf(leaderId);
    replace($("right-panel"), `<div class="head"><div><div class="title" title="${esc(item.name)} 组" style="font-size:calc(14.5px * var(--fs))">${esc(item.name)} 组</div><div class="sub">${members.length} 个下属</div><button class="member-default-entry" data-act="member-defaults" data-id="${esc(item.session_id)}">组员大脑 · ${item.member_defaults || state.ai?.member ? esc((item.member_defaults ?? state.ai.member).runtime ? runtimeInfo((item.member_defaults ?? state.ai.member).runtime).name : "自动选择") : "跟随 leader"} ›</button></div><div class="right"><button class="btn ghost" data-act="member-add-menu" data-id="${esc(item.session_id)}" style="padding:5px 9px;font-size:calc(12.5px * var(--fs))">${ic("plus", "sm")}添加</button><button class="icon-btn rp-close" data-act="right-close" aria-label="关闭架构图">${ic("x")}</button></div></div><div class="tree">${leaderNode(item, true)}<div class="members">${members.map(memberNode).join("")}</div><div class="rp-note">点任意人进入本人对话；资料与操作或右键可查看卡片。${ic("code", "xs")} VS Code，${ic("cloud", "xs")} Crew 后台。</div></div>`);
    $("right-panel").classList.toggle("show", rightOpen);
    $("rp-scrim").classList.toggle("show", rightOpen);
  }
  function attentionOpen() { return $("chat-attention")?.matches(":popover-open") === true; }
  function closeAttention(restoreFocus = false) {
    if (!attentionOpen()) return;
    $("chat-attention").hidePopover();
    if (restoreFocus) $("chat-head")?.querySelector('[data-act="attention"]')?.focus({preventScroll:true});
  }
  function positionAttention() {
    const anchor = $("chat-head")?.querySelector('[data-act="attention"]');
    if (!anchor) return;
    const panel = $("chat-attention"), rect = anchor.getBoundingClientRect();
    const width = Math.min(460, window.innerWidth - 24);
    const top = Math.max(12, Math.min(rect.bottom + 10, window.innerHeight - 180));
    panel.style.left = `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px`;
    panel.style.top = `${top}px`;
    panel.style.maxHeight = `${Math.min(640, window.innerHeight - top - 12)}px`;
  }
  function renderChat() {
    const item = readOnlyId === null ? leader(leaderId) : member(readOnlyId), group = leader(leaderId), members = membersOf(leaderId);
    const work = group === undefined ? null : groupWork(group);
    const inGoal = item => goalId == null || String(item.goal_id) === String(goalId);
    const pending = pendingOf(leaderId).filter(inGoal), held = decisionsOf(leaderId).filter(item => item.held && inGoal(item));
    const groupTodos = todosOf(leaderId).filter(inGoal);
    goals?.sync(leaderId, goalId);
    const listing = goals?.showingList() === true;
    const access = chatAccess(item, goalId == null ? null : goals?.currentGoal());
    const execution = goalId == null ? null : goals?.executionFor(item.session_id);
    const unstarted = goalId == null || execution ? null : goalChatNotes(goals?.currentGoal(), readOnlyId !== null, access.readonly);
    const chatRuntime = goalId == null ? runtimeBadge(item) : execution ? runtimeBadge(execution) : unstarted.badge ? `<span class="runtime-tag">${unstarted.badge}</span>` : "";
    const chatModel = goalId == null ? `<button class="model-tag" data-act="model-menu" data-id="${esc(item.session_id)}" aria-haspopup="menu" title="切换模型和 effort"><span class="model-label">${esc(optionsLabel(item))}</span>${ic("down", "xs")}</button>` : execution ? `<span class="model-tag" title="${esc(optionsLabel({...execution, applied:null}))} · 本任务执行配置；总对话的模型设置不会改动已有任务"><span class="model-label">${esc(optionsLabel({...execution, applied:null}))}</span></span>` : "";
    const identity = `${item.session_id}:${goalId ?? ""}:${access.readonly}:${execution?.id ?? ""}:${listing}:${access.readonlyReason ?? ""}`;
    if (mountedChat !== identity) {
      CrewChat.unmount();
      // A report being read in this goal survives a background identity change (read-only, execution, reopened round).
      const scaffold = '<div id="chat-head"></div><div id="goal-head"></div><div id="goal-chat-hint"></div><div id="chat-host" class="chat-host"></div>';
      if (goals) goals.replaceMain(scaffold); else $("main").innerHTML = scaffold;
      delete $("main").dataset.render;
      mountedChat = identity;
      if (!listing) CrewChat.mount({host:$("chat-host"), session:item, ...access, goalId, state, api, toast, beforeStop:() => confirmIdentityStop(item), onExecutionChange:() => goals?.refresh(goalId),
        async onRouted(id) { let target = (state.goals || []).find(goal => String(goal.id) === String(id)); if (!target) { await loadState(true); if (mountedChat !== identity) return; target = (state.goals || []).find(goal => String(goal.id) === String(id)); } if (target) { toast(`消息已送到任务「${target.title}」。`); navigate("chat", target.leader_session_id, item.session_id === target.leader_session_id ? null : item.session_id, target.id); } }});
    }
    $("main").classList.toggle("goal-list-mode", listing); $("chat-host").hidden = listing;
    $("goal-chat-hint").hidden = listing;
    const activeGoals = goalId == null ? (state.goals || []).filter(goal => (item.active_goal_ids || [item.active_goal_id]).some(id => String(id) === String(goal.id))) : [];
    replace($("goal-chat-hint"), activeGoals.length ? `<div class="goal-chat-hint">总对话可继续安排任务和验收。另有 ${activeGoals.length} 个任务：${activeGoals.map(goal => `<button type="button" class="text-link" data-act="active-goal" data-goal-id="${esc(goal.id)}" data-id="${esc(item.session_id)}">${esc(goal.title)} →</button>`).join(" ")}</div>` : unstarted?.hint ? `<div class="goal-chat-hint">${unstarted.hint}</div>` : "");
    const permissions = nativeApprovals?.countForChat(item.session_id, goalId, readOnlyId === null) || 0;
    const attentionCount = permissions + (readOnlyId === null ? pending.length + groupTodos.length : 0);
    const scope = `${item.session_id}:${goalId ?? ""}`;
    if (attentionScope !== scope || listing) closeAttention();
    attentionScope = scope;
    replace($("native-approval-tray"), nativeApprovals?.chatMarkup(item.session_id, goalId, readOnlyId === null) || "");
    $("attention-scope").textContent = `${readOnlyId === null ? group?.name || item.name : item.name} · ${goalId == null ? "全部任务与总对话" : goals?.currentGoal()?.title || "当前任务"}`;
    replace($("chat-head"), `<div class="head">${readOnlyId === null ? groupAvatar(item, work) : av(item.name, 30)}<div class="heading-meta"><div class="title"><span class="chat-name" title="${esc(item.name)}">${esc(item.name)}</span>${readOnlyId === null ? priorityBadge(item.priority) : ""}${chatRuntime}</div><div class="sub"><span class="chat-scope" title="${esc(readOnlyId === null ? (item.goal ?? "") : `归 ${item.group} · 成员本人对话`)}">${esc(readOnlyId === null ? (item.goal ?? "") : `归 ${item.group} · 成员本人对话`)}</span>${group === undefined ? "" : `<span class="sep"></span><span class="chat-member-stats ${work.active ? "is-working" : ""}">${members.length} 个下属 · ${work.summary}</span>`}</div></div><div class="right">${!listing ? `<button type="button" class="attention-trigger ${attentionCount ? "has-items" : ""}" data-act="attention" popovertarget="chat-attention" aria-haspopup="dialog" aria-controls="chat-attention" aria-expanded="${attentionOpen()}" aria-label="待处理，${attentionCount} 件${readOnlyId === null && held.length ? `，另有 ${held.length} 件挂起` : ""}">${ic("inbox", "sm")}<span>待处理</span>${attentionCount ? `<span class="attention-count">${attentionCount}</span>` : ""}</button>` : ""}${goalId == null ? contextControls(item) : ""}${chatModel}${readOnlyId === null ? "" : `<button class="btn ghost" data-act="leader" data-id="${esc(leaderId)}">回到 leader</button><button class="btn ghost" data-act="card" data-id="${esc(item.session_id)}">资料</button>`}<button class="icon-btn" data-act="session-menu" data-id="${esc(item.session_id)}" aria-haspopup="menu" title="会话操作" aria-label="会话操作">⋯</button>${group === undefined ? "" : `<button class="icon-btn rp-toggle" data-act="right-open" title="本组架构图" aria-label="本组架构图">${ic("grid")}</button>`}</div></div>`);
    replace($("goal-head"), group ? goals?.groupMarkup(leaderId) || "" : "");
    replace($("decision-tray"), readOnlyId !== null ? "" : `<div class="tray-in">${todoTray(groupTodos, leaderId)}${pending.length === 0 ? "" : `<section class="tray-pending"><h3 class="attention-section-title">等你拍板 <span>${pending.length}</span></h3><div class="tray-list">${pending.map(decisionCard).join("")}</div></section>`}${heldGroup(held, "tray")}</div>`);
    $("attention-empty").hidden = permissions + (readOnlyId === null ? pending.length + held.length + groupTodos.length : 0) > 0;
    if (attentionOpen()) positionAttention();
    if (readOnlyId === null && leaderTrust !== null) {
      const slot = document.createElement("span"); slot.innerHTML = leaderTrust.indicator(item);
      const anchor = $("chat-head").querySelector('[data-act="session-menu"]');
      const existing = $("chat-head").querySelector('[data-act="leader-trust"]');
      if (existing) { if (existing.outerHTML !== slot.firstElementChild.outerHTML) existing.replaceWith(slot.firstElementChild); } else if (anchor) anchor.before(slot.firstElementChild);
    }
    if (!listing) CrewChat.update(state);
  }
  // Only a queued goal without execution metadata is known to be unstarted.
  function goalChatNotes(goal, member, readonly) {
    if (member) return {badge:"还没有该成员的执行记录", hint:readonly ? "该成员在这个目标中还没有执行记录。" : "该成员尚未参与此目标。在此发送消息会启动该成员在本目标中的执行。"};
    if (goal?.status !== "queued") return {badge:"", hint:""};
    return {badge:"本目标尚未启动", hint:"开始推进会交给 leader 执行；在此发送消息也会开始本任务。先讨论可回到总会话。"};
  }
  function chatAccess(item, goal) {
    if (goal != null) {
      const reasons = {paused:"该目标已暂停，仅供查阅。请明确恢复后继续工作。",pausing:"目标正在暂停，仅供查阅。等待停止后，明确恢复才能继续。",cancelled:"本轮已取消，仅供查阅。可回总会话讨论，或再开一轮。"};
      return reasons[goal.status] ? {readonly:true,readonlyReason:reasons[goal.status]} : {readonly:false};
    }
    return item.running && item.where === "vscode"
      ? {readonly:true,readonlyReason:"该会话仍在 VS Code 或终端中运行。请先在原工具退出，再由 Crew 接管；打开记录不会启动 AI。"} : {readonly:false};
  }
  function empty(message) { return `<div class="empty">${ic("check")}<div>${message}</div></div>`; }
  function todoInstructions(value, item, ordered = false) {
    if (Array.isArray(value)) return `<${ordered ? "ol" : "ul"}>${value.map(text => `<li>${CrewChat.renderInline(String(text), item.session_id)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`;
    return value ? CrewChat.renderMarkdown(String(value), item.session_id) : '<p class="note">还没写，可以去问它。</p>';
  }
  function todoCard(item) {
    const key = String(item.id), expanded = todoExpanded.has(key), draft = todoActions.get(key), saving = todoSaving.has(key);
    const owner = state.leaders.find(person => person.session_id === item.leader_session_id || person.session_id === item.session_id);
    const blocking = item.blocking ? `<span class="todo-blocking">卡着 ${esc(typeof item.blocking === "string" ? item.blocking : item.proposer_name || "本组")}</span>` : "";
    const group = item.group || item.leader_name || "未分组", proposer = item.proposer_name || "原会话";
    return `<article class="todo-card ${expanded ? "open" : ""}" data-todo-id="${esc(key)}"><button class="todo-top" data-act="todo-toggle" data-id="${esc(key)}" aria-expanded="${expanded}" aria-controls="todo-details-${esc(key)}"><span class="todo-check" aria-hidden="true">${ic("check", "sm")}</span><span class="todo-title">${esc(item.title)}</span>${ic("down", "sm chev")}</button><div class="todo-meta"><span>${esc(group)} · ${esc(proposer)}</span><time title="${esc(date(item.created_at))}">${ago(item.created_at)}</time>${blocking}</div><div class="todo-details" id="todo-details-${esc(key)}" ${expanded ? "" : "hidden"}><section><h4>怎么做</h4><div class="markdown">${todoInstructions(item.steps, item, true)}</div></section><section><h4>完成后怎么确认</h4><div class="markdown">${todoInstructions(item.verification, item)}</div></section></div><div class="todo-card-actions"><button class="btn todo-done" data-act="todo-action" data-id="${esc(key)}" data-status="done" ${saving ? "disabled" : ""}>${ic("check", "sm")}做完了</button><button class="btn ghost" data-act="todo-action" data-id="${esc(key)}" data-status="cancelled" ${saving ? "disabled" : ""}>不做了</button><span class="gap"></span><button class="btn ghost" data-act="todo-ask" data-id="${esc(key)}" ${owner === undefined ? 'disabled title="原 leader 已结束"' : ""}>去问它 ${ic("right", "xs")}</button></div>${draft === undefined ? "" : `<form class="todo-resolution" data-todo-form="${esc(key)}"><label for="todo-note-${esc(key)}">${draft.status === "cancelled" ? "为什么不做了" : "完成备注（可选）"}</label><textarea id="todo-note-${esc(key)}" name="note" rows="2" data-preserve="todo-note-${esc(key)}" placeholder="${draft.status === "cancelled" ? "告诉它原因，方便继续安排" : "比如：已授权，可以继续验证"}" ${draft.status === "cancelled" ? "required" : ""} ${saving ? "disabled" : ""}>${esc(draft.note)}</textarea>${draft.error ? `<p class="error-line" role="alert">${esc(draft.error)}</p>` : ""}<div class="acts"><button type="button" class="btn ghost" data-act="todo-action-close" data-id="${esc(key)}" ${saving ? "disabled" : ""}>取消</button><button type="submit" class="btn ${draft.status === "cancelled" ? "" : "primary"}" ${saving ? "disabled" : ""}>${saving ? "正在保存…" : draft.status === "cancelled" ? "确认不做" : "确认做完"}</button></div></form>`}</article>`;
  }
  function todoTray(items, id) {
    if (items.length === 0) return "";
    return `<section class="todo-tray"><h3 class="attention-section-title">待你动手 <span>${items.length}</span></h3><div class="tray-list todo-tray-list" id="todo-tray-${esc(id)}">${items.map(todoCard).join("")}</div></section>`;
  }
  function renderTodos() {
    const items = openTodos();
    replace($("main"), `<div class="head big"><div><div class="title">我的待办${items.length > 0 ? ` <span class="todo-page-count">${items.length}</span>` : ""}</div><div class="sub">需要你亲自动手，做完后通知提出它的会话继续</div></div></div><div class="page"><div class="page-in todos-page">${items.length === 0 ? empty("现在没有待你动手的事") : `<div class="todo-list">${items.map(todoCard).join("")}</div>`}</div></div>`);
  }
  function renderTodoSurface() {
    if (view === "chat") renderChat();
    else if (view === "todos") renderTodos();
  }
  function todoResolvePayload(item, draft) {
    if (!item || !draft) throw new Error("这件待办已更新，请刷新后查看。");
    const note = draft.note.trim();
    if (draft.status === "cancelled" && note === "") throw new Error("请写一下不做的原因。");
    if (!["done", "cancelled"].includes(draft.status)) throw new Error("待办状态无效。");
    return {id:item.id, status:draft.status, note, expected_version:draft.version};
  }
  async function resolveTodo(id) {
    const key = String(id), item = todoById(key), draft = todoActions.get(key);
    let payload;
    try { payload = todoResolvePayload(item, draft); }
    catch (error) { if (draft) { draft.error = error.message; renderTodoSurface(); } return; }
    todoSaving.add(key); renderTodoSurface();
    try {
      const result = await api("/api/todo/resolve", payload);
      todoActions.delete(key); todoExpanded.delete(key);
      await loadState(true);
      toast(payload.status === "done" ? "已标记做完" : "已说明不做的原因", async () => {
        await api("/api/todo/undo", {id:item.id, before:result.before, version:result.version});
        await loadState(true); toast("已撤销");
      });
    } catch (error) {
      draft.error = error.message;
      await loadState(true);
      const current = todoById(key);
      if (current && current.version !== draft.version) { draft.version = current.version; draft.error = "待办内容已更新，请确认后再提交。"; todoExpanded.add(key); }
      else if (!current) toast(error.message);
    } finally { todoSaving.delete(key); renderTodoSurface(); }
  }
  function renderChannel() {
    if (!mountedChannel) {
      $("main").innerHTML = '<div class="head"><div><div class="title">交流区</div><div class="sub">所有 leader 和用户的公共频道 · 点名才叫醒</div></div><button id="channel-digest-button" class="btn">整理</button></div><div id="channel-host" class="chat-host"></div>';
      delete $("main").dataset.render;
      mountedChannel = true;
      CrewChannel.mount({host:$("channel-host"), digestButton:$("channel-digest-button"), state, api, toast, onRead:channel => { Object.assign(state.channel, {unread:channel.unread, latest_id:channel.latest_id}); renderSidebar(); }});
    }
    CrewChannel.update(state);
  }
  function renderAll() {
    const working = workingPeople([...state.leaders, ...state.members]), pending = state.decisions.filter(item => !item.held), held = state.decisions.filter(item => item.held);
    const dispatches = workingDispatches([...state.leaders, ...state.members]);
    const chip = (tab, icon, label, amount) => `<button class="pill tab ${allTab === tab ? "on" : ""}" data-act="all-tab" data-tab="${tab}">${icon}${amount === null ? "" : `<b>${amount}</b>`}${label}</button>`;
    let body;
    if (allTab === "tree") body = state.leaders.length === 0 ? `<div class="empty">${ic("grid")}<div>还没有 leader。先起一个 leader，再添加新组员或收编已有会话。</div><div class="empty-acts"><button class="btn primary" data-act="modal-leader">${ic("plus", "sm")}起 leader</button><button class="btn" data-act="view" data-view="adopt">${ic("inbox", "sm")}打开会话库</button></div></div>` : `<div class="lead-grid" tabindex="0" aria-label="各组架构，可横向滚动查看">${state.leaders.map(item => `<div class="lead-col">${leaderNode(item, true)}<div class="members">${membersOf(item.session_id).map(memberNode).join("")}</div></div>`).join("")}</div>`;
    else if (allTab === "goals") body = goals?.cards() || empty("还没有执行目标");
    else if (allTab === "native-approvals") body = `<div class="native-approval-list">${nativeApprovals?.markup() || empty("没有等待你确认的执行权限")}</div>`;
    else if (allTab === "pending") {
      const grouped = new Set();
      body = state.leaders.slice().sort((a, b) => (a.priority === null ? 4 : a.priority) - (b.priority === null ? 4 : b.priority)).map(item => { const list = pendingOf(item.session_id); list.forEach(item => grouped.add(item.id)); return list.length === 0 ? "" : `<section class="ov-group"><div class="ov-head">${av(item.name, 26)}<span class="ov-name">${esc(item.name)}</span><span class="ov-count">${list.length} 件</span><button class="btn ghost ov-go" data-act="leader" data-id="${esc(item.session_id)}">进入对话 ${ic("right", "sm")}</button></div><div class="ov-list">${list.map(decisionCard).join("")}</div></section>`; }).join("");
      const ungrouped = pending.filter(item => !grouped.has(item.id));
      if (ungrouped.length > 0) body += `<section class="ov-group"><div class="ov-head"><span class="ov-name">未分组</span><span class="ov-count">${ungrouped.length} 件</span></div><div class="ov-list">${ungrouped.map(decisionCard).join("")}</div></section>`;
      if (pending.length === 0 && held.length === 0) body = empty("没有等你拍板的事");
      body += heldGroup(held, "all");
    } else body = working.length + dispatches.length === 0 ? empty("没有正在干活的会话") : `<div class="list">${working.map(workingRow).join("")}${dispatches.map(workingDispatchRow).join("")}</div>`;
    replace($("main"), `<div class="head big"><div><div class="title">全部</div><div class="sub">${esc(projectDisplay(state.project))} 的 ${state.leaders.length} 个 leader，${state.members.length} 个下属</div></div></div><div class="page"><div class="page-in ${allTab === "tree" ? "groups-page" : ""}"><div class="summary">${openTodos().length > 0 ? `<button class="pill todo-summary" data-act="view" data-view="todos">${ic("check", "sm")}<b>${openTodos().length}</b> 件待你动手 ${ic("right", "xs")}</button>` : ""}${nativeApprovals?.count() ? chip("native-approvals", "", " 执行权限待确认", nativeApprovals.count()) : ""}${chip("tree", ic("grid", "sm"), "架构图", null)}${chip("goals", ic("check", "sm"), "目标", (state.goals || []).length)}${chip("pending", '<i class="dot waiting"></i>', " 件等你拍板", pending.length)}${chip("working", '<i class="dot working"></i>', " 个在干活", working.length + dispatches.length)}<button class="pill tab" data-act="view" data-view="adopt">${ic("inbox", "sm")}会话库 · 查找与接入 ${ic("right", "xs")}</button></div>${body}</div></div>`);
  }
  function renderAdopt() {
    if (sessionLibrary === null) sessionLibrary = CrewSessionLibrary.create({api, toast,
      preferredLeader:() => leader(adoptLeaderId)?.name || "", changed:() => loadState(true),
      open:item => { const owner = item.role === "leader" ? item : state.leaders.find(person => person.name === item.group);
        if (owner) navigate("chat", owner.session_id, item.role === "leader" ? null : item.session_id); }});
    sessionLibrary.mount($("main"), state);
  }

  function quotaCard(label, limit) {
    return `<div class="qcard"><div class="lbl"><span>${label}</span><span>账户</span></div>${limit === null ? '<div class="big" style="font-size:calc(22px * var(--fs))">未采集</div>' : `${limit.used_percent === null ? `<div class="big" style="font-size:calc(22px * var(--fs))">${quotaText(limit)}</div>` : `<div class="big">${count(limit.used_percent)}%<small>已用</small></div><div class="bar"><i style="width:${limit.used_percent}%;${limit.used_percent >= 80 ? "background:var(--warn)" : ""}"></i></div>`}${crewQuota(limit, true)}<div class="rs">${date(limit.resets_at)} 重置 · ${time(limit.captured_at)} 采集${limit.expired ? " · 采集已过期" : ""}</div>`}</div>`;
  }
  function renderUsage() {
    let table = '<p class="note">正在读取用量…</p>', total = "";
    if (usage !== null) {
      const totalTokens = usage.tree.reduce((sum, row) => sum + row.tokens, 0), totalCost = usage.tree.some(row => row.cost === null) ? null : usage.tree.reduce((sum, row) => sum + row.cost, 0);
      const codexTokens = usage.tree.reduce((sum, row) => sum + (row.codex_tokens || 0), 0);
      total = `Claude ${tokens(totalTokens)} token${codexTokens > 0 ? ` · Codex ${tokens(codexTokens)} token` : ""} · Claude 折合 ${money(totalCost)}`;
      const share = amount => `<div class="share">${count(amount)}%<span class="b"><i style="width:${Math.min(100, amount)}%"></i></span></div>`;
      const usageTokens = row => `${tokens(row.tokens)}${row.codex_runs > 0 ? `<div class="usage-codex-count">Codex ${tokens(row.codex_tokens)} · ${row.codex_runs} 次</div>` : ""}`;
      if (usageTab === "tree") table = `<table class="utable"><thead><tr><th>谁</th><th class="num">Claude Token</th><th class="num">Claude 折合</th><th class="num">Claude 占比</th></tr></thead><tbody>${usage.tree.map(row => {
        const key = row.leader_session_id === null ? "ungrouped" : row.leader_session_id, open = !usageCollapsed.has(key);
        const selfShare = totalTokens === 0 ? 0 : row.self.tokens / totalTokens * 100;
        return `<tr class="grp"><td><button class="cell-name table-toggle" data-act="usage-fold" data-id="${esc(key)}" aria-expanded="${open}">${ic(open ? "down" : "right", "xs")}${av(row.name, 22)}${esc(row.name)}</button></td><td class="num">${usageTokens(row)}</td><td class="num">${money(row.cost)}</td><td class="num">${share(row.share)}</td></tr>${open ? `${row.leader_session_id === null ? "" : `<tr class="sub"><td><span class="cell-name">${av(row.name, 20, true)}${esc(row.name)}（leader 本身）</span></td><td class="num">${usageTokens(row.self)}</td><td class="num">${money(row.self.cost)}</td><td class="num">${share(selfShare)}</td></tr>`}${row.members.map(item => `<tr class="sub"><td><span class="cell-name">${av(item.name, 20, true)}${esc(item.name)}</span></td><td class="num">${usageTokens(item)}</td><td class="num">${money(item.cost)}</td><td class="num">${share(item.share)}</td></tr>`).join("")}` : ""}`;
      }).join("")}</tbody></table>`;
      else if (usageTab === "daily") table = `<table class="utable"><thead><tr><th>日期</th><th>会话</th><th class="num">Token</th><th class="num">折合</th></tr></thead><tbody>${usage.daily.map(row => `<tr><td>${esc(row.date)}</td><td>${esc(row.display_name)}</td><td class="num">${tokens(row.total_tokens)}</td><td class="num">${money(row.cost_usd)}</td></tr>`).join("")}</tbody></table>`;
      else table = `<table class="utable"><thead><tr><th>模型</th><th>来源</th><th class="num">输入</th><th class="num">输出</th><th class="num">合计</th></tr></thead><tbody>${usage.models.map(row => `<tr><td>${esc(row.model)}</td><td>${esc(row.source)}</td><td class="num">${tokens(row.input_tokens)}</td><td class="num">${tokens(row.output_tokens)}</td><td class="num">${tokens(row.total_tokens)}</td></tr>`).join("")}</tbody></table>`;
    }
    replace($("main"), `<div class="head big"><div><div class="title">用量</div><div class="sub">${esc(projectDisplay(state.project))} · ${{today:"今天", week:"本周", all:"全部"}[period]}</div></div><div class="right" style="gap:10px"><div class="seg">${[["today", "今天"], ["week", "本周"], ["all", "全部"]].map(([key, name]) => `<button class="${period === key ? "on" : ""}" data-act="usage-period" data-period="${key}">${name}</button>`).join("")}</div><a class="btn" href="${esc(queryPath(`/api/usage/export?period=${period}&what=${usageTab === "daily" ? "daily" : "sessions"}`))}" download>${ic("download", "sm")}导出</a></div></div><div class="page"><div class="page-in"><div class="qcards">${quotas().map(([label, limit]) => quotaCard(label, limit)).join("")}</div><div class="usage-tabs"><div class="seg">${[["tree", "按结构"], ["daily", "按天"], ["models", "按模型"]].map(([key, name]) => `<button class="${usageTab === key ? "on" : ""}" data-act="usage-tab" data-tab="${key}">${name}</button>`).join("")}</div><span style="font-size:calc(12.5px * var(--fs));color:var(--text-3)">${total}</span></div>${table}<div class="note">折合金额按 API 价换算，只用于比较；额度是账户级；界面字体 HarmonyOS Sans（华为，按其许可证随附）</div></div></div>`);
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
  function openCard(id) { cardReturn = focusDescriptor(document.activeElement); cardId = id; handoff = null; renderCard(); renderRight(); requestAnimationFrame(() => $("card").querySelector('[data-act="card-close"]').focus({preventScroll:true})); }
  function closeCard() { cardId = null; handoff = null; $("card").classList.remove("show"); $("card-back").classList.remove("show"); hideMenu(); renderRight(); returnFocus(cardReturn); }
  function renderCard() {
    if (cardId === null) return;
    const item = member(cardId);
    if (item === undefined) { closeCard(); return; }
    const background = item.where === "bg", canStop = background && item.status === "working";
    const stopReason = background ? "这一轮没在工作" : item.where === "vscode" ? "开在 VS Code 里，去那边停" : "会话没在运行";
    const name = esc(item.name);
    replace($("card"), `<div class="card-head" data-session-menu="${esc(item.session_id)}" tabindex="0">${av(item.name, 44, true)}<div style="min-width:0;flex:1"><div class="nm">${name}${contextControls(item)}</div><div class="meta">${runtimeBadge(item)}${st(item.status)}<span>${ic(background ? "cloud" : item.where === "vscode" ? "code" : "clock", "xs")} ${background ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}</span><span>归 ${esc(item.group)}</span>${memberModel(item)}</div></div><button class="icon-btn" data-act="card-close" aria-label="关闭下属卡片">${ic("x")}</button></div>
      ${background && item.activity !== null ? `<div class="card-activity">${memberActivity(item)}</div>` : ""}
      <div class="card-actions"><button class="btn primary" data-act="open-vscode" ${editorHandoffReason(item) ? `disabled title="${esc(editorHandoffReason(item))}"` : item.status === "working" ? 'disabled title="会话正在工作，请等它空闲后再打开"' : ""}>${ic("code", "sm")}在 VS Code 打开</button><span class="gap"></span><button class="icon-btn" data-act="rename" title="改名" aria-label="改名">${ic("pencil")}</button><button class="icon-btn" data-act="move-menu" title="换组" aria-label="换组">${ic("swap")}</button><button class="icon-btn" data-act="archive" title="归档" aria-label="归档">${ic("archive")}</button><button class="icon-btn" data-act="stop" ${canStop ? "" : "disabled"} title="${canStop ? "停下这一轮" : stopReason}" aria-label="停下这一轮">${ic("stop")}</button><button class="icon-btn" data-act="more-menu" title="更多" aria-label="更多">${ic("more")}</button></div>
      ${goalId == null ? runtimeSwitch?.entry(item) || "" : '<p class="editor-handoff-note">更换执行工具请回本组总对话，并先暂停该人员仍在推进的全部目标。</p>'}
      ${editorHandoffReason(item) ? `<p class="editor-handoff-note">${esc(editorHandoffReason(item))}</p>` : ""}
      ${handoff === null ? "" : `<div class="step"><div class="row"><span class="n">${ic("check", "xs")}</span><span>已打开目录，等 VS Code 窗口准备好<br><code>${esc(handoff.cwd)}</code></span></div><div class="row"><span class="n todo">2</span><span style="flex:1">窗口好了以后，${handoff.needs_confirmation ? "停掉后台或终端这一份，在 VS Code 里接上同一个会话" : "在 VS Code 里接上同一个会话"}<br><button class="btn primary" data-act="handoff-go" style="margin-top:8px">窗口好了，接上</button><button class="btn ghost" data-act="handoff-cancel" style="margin-top:8px">先不了</button></span></div></div>`}
      <div class="card-body"><div class="sec"><h4>负责范围</h4><p>${item.scope === null || item.scope === "" ? "还没写" : CrewChat.renderInline(item.scope, item.session_id)}</p></div><div class="sec"><h4>现在在做</h4><p>${item.task === null ? "还没写" : CrewChat.renderInline(item.task, item.session_id)}</p></div><div class="sec"><h4>完成标准</h4><p>${item.done_when === null || item.done_when === "" ? "还没写" : CrewChat.renderInline(item.done_when, item.session_id)}</p></div><div class="sec"><h4>最近进展</h4>${item.progress.length === 0 ? '<p class="note">还没写</p>' : `<div class="tl">${item.progress.map(progress => `<div class="tl-i"><span class="t" title="${esc(date(progress.at))}">${time(progress.at)}</span><span>${CrewChat.renderInline(progress.text, item.session_id)}</span></div>`).join("")}</div>`}</div><div class="sec"><h4>临时子代理与派活</h4><div class="chips">${item.codex.map(task => codexMarkup(task, "chip", item.session_id)).join("")}${item.codex_older > 0 ? `<div class="chip" style="color:var(--text-3)">更早 ${item.codex_older} 个</div>` : ""}${item.codex.length === 0 && item.codex_older === 0 ? '<p class="note">还没有</p>' : ""}</div></div><div class="sec"><h4>设备和文件</h4><div class="chips">${item.leases.map(lease => `<div class="chip" title="${esc(lease.purpose)} · ${date(lease.taken_at)} 至 ${lease.until_at === null ? "未写归还时间" : date(lease.until_at)}">${ic("device", "xs")}${esc(lease.resource)}<span class="grow"></span>${esc(lease.purpose)}</div>`).join("")}${item.claims.map(claim => `<div class="chip" title="${esc(claim.pattern)}${claim.note === null || claim.note === "" ? "" : ` · ${esc(claim.note)}`}">${ic("file", "xs")}<code>${esc(claim.pattern)}</code></div>`).join("")}${item.leases.length === 0 && item.claims.length === 0 ? '<p class="note">没有占用的设备和文件</p>' : ""}</div></div></div>
      <div class="card-foot"><button class="text-link" data-act="member-history">${ic("msg", "xs")}进入本人对话</button><span class="gap"></span>${ic("branch", "xs")}<code title="${item.branch === null ? "无分支" : esc(item.branch)}">${item.branch === null ? "无分支" : esc(item.branch)}</code><code title="${esc(item.session_id)}">${esc(item.session_id)}</code></div>`);
    for (const button of $("card").querySelectorAll('[data-act]')) button.dataset.id = item.session_id;
    $("card").classList.add("show"); $("card-back").classList.add("show");
  }
  const sessionItem = id => state.leaders.concat(state.members).find(item => item.session_id === id);
  let menuAnchor = null;
  function modelMenu(item) {
    const id = item.session_id, options = currentOptions(item), runtime = runtimeInfo(item), efforts = modelEfforts(item, item.model ?? options.model), unsupported = efforts.length === 0;
    const error = !runtime.available ? runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。` : null;
    return `<div class="menu-runtime">${runtimeBadge(item)}<span>执行工具</span></div>${error ? `<div class="runtime-error" role="status">${esc(error)}</div>` : ""}<div role="group" aria-labelledby="menu-model-label"><div class="mtitle" id="menu-model-label">模型</div>${[{id:null, name:"默认"}].concat(runtime.models || []).map(model => `<button role="menuitemradio" aria-checked="${item.model === model.id}" data-act="set-model" data-id="${esc(id)}" data-model="${model.id === null ? "" : esc(model.id)}" ${runtime.available ? "" : "disabled"}><span class="model-check">${item.model === model.id ? ic("check", "sm") : ""}</span>${esc(model.name)}</button>`).join("")}</div><div role="group" aria-labelledby="menu-effort-label" aria-disabled="${unsupported}"><div class="mtitle" id="menu-effort-label">effort${unsupported ? " · 此模型不支持" : ""}</div>${efforts.map(effort => `<button role="menuitemradio" aria-checked="${options.effort === effort}" data-act="set-effort" data-id="${esc(id)}" data-effort="${esc(effort)}" ${runtime.available ? "" : "disabled"}><span class="model-check">${options.effort === effort ? ic("check", "sm") : ""}</span>${esc(effort)}</button>`).join("")}</div>${Array.isArray(state.runtimes) ? `<button role="menuitem" class="runtime-refresh" data-act="runtimes-refresh" data-id="${esc(id)}" ${runtimesLoading ? "disabled" : ""}>${runtimesLoading ? "检测中…" : "重新检测执行工具和模型"}</button>` : ""}`;
  }
  function modelControls(item) {
    const id = item.session_id;
    return modelMenu(item) + `<div class="menu-context">${item.where === "vscode" ? '<span class="context-hint">去 VS Code 里输入 /compact</span>' : `<button role="menuitem" data-act="compact" data-id="${esc(id)}" ${compactBusy(item) ? 'disabled title="正在干活，等这一轮结束再压缩"' : ""}>压缩上下文（${item.context === null ? "当前用量未知" : `当前 ${Math.round(item.context.used / item.context.max * 100)}%`}）</button>`}</div>`;
  }
  function menuButton(item, act, label, reason = "", danger = false, extra = "") {
    return `<button role="menuitem" data-act="${act}" data-id="${esc(item.session_id)}" ${reason ? `disabled title="${esc(reason)}"` : ""} ${danger ? 'class="danger"' : ""} ${extra}>${label}</button>`;
  }
  function submenu(item, label, html, danger = false) {
    return `<div class="menu-branch">${menuButton(item, "submenu", `${label}<span class="grow"></span>▸`, "", danger, 'aria-haspopup="menu" aria-expanded="false"')}<div class="menu sub-menu" role="menu" hidden>${html}</div></div>`;
  }
  function sessionMenu(item) {
    const isLeader = state.leaders.includes(item), stopReason = item.where !== "bg" ? item.where === "vscode" ? "开在 VS Code 里，去那边停" : "会话没在运行" : !compactBusy(item) ? "这一轮没在工作" : "";
    const compactReason = item.where === "vscode" ? "去 VS Code 里输入 /compact" : compactBusy(item) ? "正在干活，等这一轮结束再压缩" : "";
    const btn = (act, label, reason = "", danger = false, extra = "") => menuButton(item, act, label, reason, danger, extra);
    let html = isLeader ? btn("leader", "打开对话") : btn("member-history", "进入本人对话") + btn("card", "资料与操作") + btn("open-vscode", "在 VS Code 打开", editorHandoffReason(item) || (item.status === "working" ? "会话正在工作，请等它空闲后再打开" : ""));
    if (runtimeSwitch) html += btn("runtime-switch", "更换执行工具…", goalId == null ? runtimeSwitch.reason(item) : "请回到本组总对话，先暂停该人员仍在推进的全部目标，再更换执行工具");
    html += btn("stop", "停下这一轮", stopReason) + btn("compact", "压缩上下文", compactReason) + submenu(item, "模型和 effort", modelMenu(item)) + btn("comments", "评论") + btn("session-rename", "改名");
    if (isLeader) {
      html += btn("member-create", "新建组员") + btn("member-defaults", "组员默认大脑");
      html += btn("compact-department", "压缩整个部门…");
      html += btn("leader-trust", "本组信任模式") + btn("leader-trust-report", "本组信任报告");
      const siblings = sortableLeaders(item), index = siblings.indexOf(item), members = membersOf(item.session_id), busy = members.filter(compactBusy).length;
      html += submenu(item, "设为优先", [1, 2, 3, 0].map(rank => `<button role="menuitemradio" aria-checked="${item.priority === (rank === 0 ? null : rank)}" data-act="set-priority" data-id="${esc(item.session_id)}" data-rank="${rank}"><span class="model-check">${item.priority === (rank === 0 ? null : rank) ? ic("check", "sm") : ""}</span>${rank === 0 ? "取消优先" : `第 ${rank} 优先`}</button>`).join(""));
      html += btn("leader-up", "上移", index === 0 ? "已经在最上面" : "") + btn("leader-down", "下移", index === siblings.length - 1 ? "已经在最下面" : "") + btn("group-tree", "看本组架构图") + btn("open-vscode", "转到 VS Code 里聊", editorHandoffReason(item) || (compactBusy(item) ? "会话正在工作，请等它空闲后再打开" : ""));
      if (item.is_git_admin) html += `<button role="menuitem" class="git-menu-link" data-act="git-settings">项目 Git 设置</button>`;
      else html += submenu(item, "结束这个 leader", members.length === 0 ? btn("close-group", "结束（组里没有下属）", "", true, 'data-with-members="false"') : btn("close-group", `<span>连下属一起归档<small>连 ${members.length} 个下属一起归档，其中 ${busy} 个在干活</small></span>`, "", true, 'data-with-members="true"') + btn("close-group", `<span>只结束 leader，下属退回待收编<small>${members.length} 个下属退回待收编，其中 ${busy} 个在干活</small></span>`, "", true, 'data-with-members="false"'), true);
    } else {
      const groups = state.leaders.filter(group => group.session_id !== item.leader_session_id);
      html += groups.length ? submenu(item, "换组", groups.map(group => btn("move-to", esc(group.name), "", false, `data-group="${esc(group.name)}"`)).join("")) : btn("move-menu", "换组", "没有其他协作组");
      html += btn("leave-group", "退组", item.leader_session_id === null ? "当前没有加入协作组" : "") + btn("archive", "归档", "", true) + btn("remove-session", "移出 Crew", "", true);
      if (item.where === "offline") html += btn("wake-session", "叫醒");
    }
    if (editorHandoffReason(item)) html += `<p class="editor-handoff-note">${esc(editorHandoffReason(item))}</p>`;
    return html;
  }
  function placeMenu(menu, x, y) {
    menu.style.left = `${Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, x))}px`;
    menu.style.top = `${Math.max(8, Math.min(innerHeight - menu.offsetHeight - 8, y))}px`;
  }
  function menuItems(menu) { return [...menu.querySelectorAll("button:not(:disabled)")].filter(button => button.closest('[role="menu"]') === menu); }
  function showMenu(anchor, html, point = null) {
    hideMenu(); menuAnchor = anchor;
    const menu = $("menu"), rect = anchor.getBoundingClientRect(); menu.innerHTML = html; menu.classList.add("show");
    placeMenu(menu, point === null ? rect.right - menu.offsetWidth : point.x, point === null ? rect.bottom + 6 : point.y);
    menuItems(menu)[0]?.focus({preventScroll:true});
  }
  function closeSubmenu(menu) {
    for (const child of menu.querySelectorAll('.sub-menu')) { child.hidden = true; child.classList.remove('show'); child.previousElementSibling.setAttribute('aria-expanded', 'false'); }
  }
  function openSubmenu(button) {
    const menu = button.closest('[role="menu"]'); closeSubmenu(menu);
    const child = button.nextElementSibling, rect = button.getBoundingClientRect();
    child.hidden = false; child.classList.add('show'); button.setAttribute('aria-expanded', 'true');
    placeMenu(child, rect.right + child.offsetWidth + 8 > innerWidth ? rect.left - child.offsetWidth : rect.right + 4, rect.top);
    menuItems(child)[0]?.focus({preventScroll:true});
  }
  function hideMenu(restore = false) {
    const menu = $("menu"); closeSubmenu(menu); menu.classList.remove("show");
    if (restore && menuAnchor?.isConnected) menuAnchor.focus({preventScroll:true});
  }
  function openSessionMenu(anchor, id, point = null) { showMenu(anchor, sessionMenu(sessionItem(id)), point); }
  function sessionSurface(target) { return target.closest('[data-session-menu],#sidebar [data-act="leader"],[data-act="card"]'); }
  function openRename(id) {
    const item = sessionItem(id), isLeader = state.leaders.includes(item); hideMenu(); dialogReturn = focusDescriptor(document.activeElement);
    $("backdrop").innerHTML = `<form class="modal rename-modal" id="session-rename-form" data-id="${esc(id)}" data-original="${esc(item.name)}" role="dialog" aria-modal="true" aria-labelledby="rename-title"><div class="rename-heading"><span class="rename-icon">${ic("pencil")}</span><div><h3 id="rename-title">给${isLeader ? " leader " : "组员"}改名</h3><p class="d">${isLeader ? "本组 leader" : esc(item.group) + " 组的组员"}</p></div><button class="icon-btn" type="button" data-act="modal-close" aria-label="关闭改名">${ic("x")}</button></div><div class="rename-current">${av(item.name,32,!isLeader)}<div><span>当前名字</span><strong>${esc(item.name)}</strong></div></div><div class="field"><label for="session-new-name">新名字</label><input id="session-new-name" name="name" value="${esc(item.name)}" placeholder="取一个好辨认的名字" required maxlength="512" autocomplete="off"></div><p class="error-line" id="rename-error" role="alert"></p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="submit" disabled>保存名字</button></div></form>`;
    $("backdrop").classList.add("show"); $("session-rename-form").elements.name.select();
  }
  async function saveRename(form) {
    const name = form.elements.name.value.trim();
    if (!name || name === form.dataset.original || form.dataset.saving === "true") return;
    form.dataset.saving = "true"; $("rename-error").textContent = "";
    for (const control of form.querySelectorAll("input,button")) control.disabled = true;
    let nativeWarnings = [];
    try {
      const result = await api("/api/session/rename", {session_id:form.dataset.id,name});
      nativeWarnings = result.native_warnings || [];
    } catch (error) {
      form.dataset.saving = "false"; $("rename-error").textContent = error.message;
      for (const control of form.querySelectorAll("input,button")) control.disabled = false;
      form.elements.name.focus();
      return;
    }
    form.dataset.saving = "false"; closeBackdrop();
    try { await loadState(true); toast(nativeWarnings.length ? "名字已更新；" + nativeWarnings.join("；") : "名字已更新"); }
    catch (error) { toast("名字已更新，刷新失败：" + error.message); }
  }
  function openHandoff(id, result) {
    dialogReturn = focusDescriptor(document.activeElement); handoff = {...result, session_id:id};
    $("backdrop").innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="handoff-title"><h3 id="handoff-title">在 VS Code 接续对话</h3><p>已打开目录，等 VS Code 窗口准备好：<code>${esc(result.cwd)}</code></p><p>${result.needs_confirmation ? "接续时会停掉后台或终端这一份。" : "接上同一个会话。"}</p><div class="acts"><button class="btn ghost" data-act="modal-close">先不了</button><button class="btn primary" data-act="session-handoff" data-id="${esc(id)}">窗口好了，接上</button></div></div>`;
    $("backdrop").classList.add("show"); $("backdrop").querySelector('[data-act="session-handoff"]').focus();
  }
  function closeBackdrop() { if ($("session-rename-form")?.dataset.saving === "true") return; const resolve = stopConfirmation; stopConfirmation = null; resolve?.(false); auxiliaryDialog += 1; commentsSession = null; $("backdrop").classList.remove("show"); $("backdrop").innerHTML = ""; delete $("backdrop").dataset.render; returnFocus(dialogReturn); }
  function confirmIdentityStop(item) {
    const affected = (state.goals || []).filter(goal => goal.leader_session_id === item.session_id && (["running","pausing","waiting_user"].includes(goal.status) || String(goal.id) === String(item.active_goal_id)));
    if (!affected.length) return Promise.resolve(true);
    dialogReturn = focusDescriptor(document.activeElement);
    $("backdrop").innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="stop-person-title"><h3 id="stop-person-title">停止「${esc(item.name)}」？</h3><p class="d">将停止此人的 Crew 执行，并暂停它负责的以下任务及相关成员执行。对话、草稿和交付物会保留。</p><ul>${affected.map(goal => `<li>${esc(goal.title)}</li>`).join("")}</ul><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">返回</button><button class="btn" type="button" data-act="confirm-stop-person">确认停止此人</button></div></div>`;
    $("backdrop").classList.add("show"); $("backdrop").querySelector('[data-act="modal-close"]').focus();
    return new Promise(resolve => { stopConfirmation = resolve; });
  }
  function hideSettings() { if (settingsOpen) closeSettings(); }
  function closeSettings() {
    settingsOpen = false; settingsUI?.hide(); $("app").inert = false;
    if (state !== null && project !== null) render();
    settingsReturn?.focus?.({preventScroll:true}); settingsReturn = null;
  }
  function openSettings(category = "ai") {
    closeAttention();
    settingsReturn = document.activeElement; settingsOpen = true;
    if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; }
    if (mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    CrewGit.unmount(); goals?.closeDrawer(); if (cardId !== null) closeCard(); hideMenu();
    $("app").inert = true; settingsUI?.open(category);
    $("settings-surface").querySelector(`[data-settings-category="${category}"]`)?.focus();
  }
  function settingsConnections(host) {
    return window.CrewConnections.mount(host, {
      read:() => api("/api/connections"),
      configure:body => api("/api/connections/configure", body),
      action:body => api("/api/connections/action", body),
      preferences:body => api("/api/connections/preferences", body),
      away:value => api("/api/notifications/away", {away:value})
    });
  }
  function settingsStorage(host) {
    return window.CrewStorage.mount(host, {
      read:() => api('/api/storage'),
      preview:body => api('/api/storage/preview', body),
      clean:body => api('/api/storage/clean', body),
      restore:body => api('/api/storage/restore', body),
      emptyTrash:body => api('/api/storage/trash/empty', body),
      savePolicy:body => api('/api/storage/policy', body)
    });
  }
  function openWorkspaceSettings() {
    closeAttention();
    if (window.navigator?.userAgent.includes("Electron/")) { location.assign(`/?workspace-settings=${encodeURIComponent(project)}`); return; }
    workspaceOpen = true;
    if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; }
    if (mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    CrewGit.unmount(); goals?.closeDrawer(); if (cardId !== null) closeCard(); hideMenu();
    void workspaceSettings.open(project);
  }
  function sessionComments(id) {
    if (!commentsCache.has(id)) commentsCache.set(id, {comments:[], draft:"", error:null, loading:false, saving:false, request:null});
    return commentsCache.get(id);
  }
  function commentsMarkup(item, data) {
    const isLeader = state.leaders.some(person => person.session_id === item.session_id), owner = isLeader ? item : leader(item.leader_session_id);
    const destination = owner ? `${owner.name} 组 · 评论会转告 leader` : "这条评论会保存在原会话下";
    return `<div class="modal comments-modal" id="comments-dialog" data-session-id="${esc(item.session_id)}" role="dialog" aria-modal="true" aria-labelledby="comments-title"><div class="report-head"><h3 id="comments-title">${esc(item.name)}${isLeader ? "组" : ""}的评论</h3><button class="icon-btn" data-act="modal-close" aria-label="关闭评论">${ic("x")}</button></div><p class="d">${esc(destination)}</p><div class="comments-list" role="log" aria-label="评论记录">${data.comments.length === 0 ? `<p class="note">${data.loading ? "正在读取评论…" : "还没有评论。可以说说你的想法或补充要求。"}</p>` : data.comments.map(comment => `<article class="comment-entry"><div class="comment-meta"><span>${esc(comment.author_name || comment.author || "用户")}</span>${comment.created_at ? `<time title="${esc(date(comment.created_at))}">${ago(comment.created_at)}</time>` : ""}</div><div class="markdown">${CrewChat.renderMarkdown(comment.text, item.session_id)}</div></article>`).join("")}</div>${data.error ? `<p class="error-line" role="alert">${esc(data.error)}</p>` : ""}<form id="comments-form" data-session-id="${esc(item.session_id)}"><label class="sr-only" for="comment-text">写评论</label><textarea id="comment-text" name="text" data-preserve="comment-draft-${esc(item.session_id)}" rows="3" placeholder="写给这个组或成员，补充背景、要求或反馈" required ${data.saving ? "disabled" : ""}>${esc(data.draft)}</textarea><div class="acts"><button class="btn ghost" type="button" data-act="comments-refresh" data-id="${esc(item.session_id)}" ${data.loading ? "disabled" : ""}>刷新评论</button><button class="btn primary" type="submit" ${data.saving ? "disabled" : ""}>${data.saving ? "正在发送…" : "提交评论"}</button></div></form></div>`;
  }
  function renderComments(id, initial = false) {
    const item = sessionItem(id), dialog = $("comments-dialog");
    if (!item || commentsSession !== id || !initial && dialog?.dataset.sessionId !== id) return;
    if (initial) delete $("backdrop").dataset.render;
    replace($("backdrop"), commentsMarkup(item, sessionComments(id)));
  }
  async function loadComments(id) {
    const data = sessionComments(id);
    if (data.loading) return data.request;
    data.loading = true; data.error = null; renderComments(id);
    data.request = (async () => {
      try { const result = await api(`/api/comments?session_id=${encodeURIComponent(id)}`); if (!Array.isArray(result.comments)) throw new Error("评论列表格式无效"); data.comments = result.comments; }
      catch (error) { data.error = error.message; }
      finally { data.loading = false; data.request = null; renderComments(id); }
    })();
    return data.request;
  }
  function openComments(id) {
    if (!sessionItem(id)) return;
    dialogReturn = focusDescriptor(document.activeElement); hideSettings(); commentsSession = id; auxiliaryDialog += 1;
    renderComments(id, true); $("backdrop").classList.add("show"); $("comment-text").focus(); loadComments(id);
  }
  async function postComment(id, text) {
    const data = sessionComments(id), value = text.trim();
    if (value === "" || data.saving) return;
    data.draft = text; data.error = null; data.saving = true; renderComments(id);
    try { await api("/api/comments", {session_id:id, text:value}); data.draft = ""; const form = $("comments-form"); if (form?.dataset.sessionId === id) form.elements.text.value = ""; if (data.request) await data.request; await loadComments(id); toast("评论已保存"); }
    catch (error) { data.error = error.message; }
    finally { data.saving = false; renderComments(id); }
  }
  function openCompact(id) {
    const item = state.leaders.concat(state.members).find(item => item.session_id === id);
    hideMenu(); dialogReturn = focusDescriptor(document.activeElement);
    $("backdrop").innerHTML = `<div class="modal compact-modal" role="dialog" aria-modal="true" aria-labelledby="compact-title"><h3 id="compact-title">压缩「${esc(item.name)}」的上下文？</h3><p class="d">压缩后它只保留摘要，细节以白板为准。</p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="button" data-act="compact-confirm" data-id="${esc(id)}">压缩</button></div></div>`;
    $("backdrop").classList.add("show"); $("backdrop").querySelector('[data-act="modal-close"]').focus();
  }
  async function compactContext(id) {
    const item = sessionItem(id);
    if (!item || compactions.has(id)) return;
    if (item.where === "vscode") { toast(`「${item.name}」正在原工具中运行，请在那边输入 /compact。`); return; }
    const pending = {phase:"submitting"};
    compactions.set(id, pending); render();
    let closeStream;
    const finish = event => {
      if (compactions.get(id) !== pending) return;
      compactions.delete(id); render();
      if (event.type !== "compact") toast(`「${item.name}」：${event.text || event.reason || "会话已离线，压缩未完成"}`);
    };
    try {
      closeStream = await CrewChat.watchCompact(id, {state, api, toast:message => toast(`「${item.name}」：${message}`), onFinish:finish});
      const result = await api("/api/session/compact", {session_id:id, after_turn:true});
      if (compactions.get(id) === pending) {
        pending.phase = result.queued ? "queued" : "compacting";
        toast(`「${item.name}」：${result.message || "正在压缩上下文…"}`);
      }
    } catch (error) {
      closeStream?.();
      if (compactions.get(id) === pending) { compactions.delete(id); render(); }
      throw error;
    }
    await loadState(true);
  }
  function renderLeaderRuntime() {
    const form = $("leader-form");
    if (form === null) return;
    if (form.dataset.runtimeExplicit !== "true") {
      const selected = taskRuntime("leader"), selection = state?.ai?.leader ?? state?.ai?.default;
      if (form.elements.runtime.value !== selected) { form.elements.runtime.value = selected; form.elements.model.value = selection?.model || ""; form.elements.effort.value = selection?.effort || ""; }
    }
    const runtime = runtimeInfo(form.elements.runtime.value);
    $("leader-runtime-options").innerHTML = runtimeList().map(item => `<button type="button" class="model-option runtime-option ${item.id === runtime.id ? "on" : ""}" data-act="leader-runtime" data-runtime="${esc(item.id)}" aria-pressed="${item.id === runtime.id}">${esc(item.name)}${item.available ? "" : '<small>未就绪</small>'}</button>`).join("");
    const status = $("leader-runtime-status");
    status.className = !runtime.available || runtimesError ? "runtime-error" : "runtime-note";
    status.textContent = runtimesError || (!runtime.id ? runtimePrompt() : !runtime.available ? runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。` : runtimesLoading ? "正在检测执行工具和模型…" : "");
    const refresh = form.querySelector('[data-act="runtimes-refresh"]');
    refresh.hidden = !Array.isArray(state.runtimes);
    refresh.disabled = runtimesLoading;
    const models = [...(runtime.models || [])];
    if (form.elements.model.value && !models.some(model => model.id === form.elements.model.value)) models.push({id:form.elements.model.value, name:`自定义：${form.elements.model.value}`});
    $("leader-model-options").innerHTML = [{id:"", name:"原生默认"}].concat(models).map(model => `<button type="button" class="model-option ${form.elements.model.value === model.id ? "on" : ""}" data-act="leader-model" data-model="${esc(model.id)}" aria-pressed="${form.elements.model.value === model.id}" ${runtime.available ? "" : "disabled"}>${esc(model.name)}</button>`).join("");
    const efforts = modelEfforts(runtime.id, form.elements.model.value || null), unsupported = efforts.length === 0;
    $("leader-effort-note").hidden = !unsupported;
    $("leader-effort-options").setAttribute("aria-disabled", String(unsupported || !runtime.available));
    const shownEfforts = ["", ...efforts]; if (form.elements.effort.value && !shownEfforts.includes(form.elements.effort.value)) shownEfforts.push(form.elements.effort.value);
    $("leader-effort-options").innerHTML = shownEfforts.map(effort => `<button type="button" class="model-option ${form.elements.effort.value === effort ? "on" : ""}" data-act="leader-effort" data-effort="${esc(effort)}" aria-pressed="${form.elements.effort.value === effort}" ${!runtime.available ? "disabled" : ""}>${effort === "" ? "原生默认" : esc(effort)}</button>`).join("");
    form.querySelector('[type="submit"]').disabled = !runtime.available || operationIds.has("leader");
  }
  function leaderPayload(form) {
    const runtime = runtimeInfo(form.elements.runtime.value), model = form.elements.model.value || null, effort = form.elements.effort.value || null;
    if (!runtime.id) throw new Error(runtimePrompt());
    if (!runtime.available) throw new Error(runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。`);
    const knownModel = (runtime.models || []).find(item => item.id === model);
    if (effort !== null && knownModel && Array.isArray(knownModel.efforts) && !knownModel.efforts.includes(effort)) throw new Error("这个模型不支持所选 effort，请重新选择。");
    return {name:form.elements.name.value.trim(), task:form.elements.task.value.trim(), runtime:runtime.id, model, effort};
  }
  function selectLeaderOption(form, field, value) {
    form.dataset.runtimeExplicit = "true";
    form.elements[field].value = value;
    if (field === "runtime") { form.elements.model.value = ""; form.elements.effort.value = ""; }
  }
  function openModalLeader() {
    dialogReturn = focusDescriptor(document.activeElement);
    const selection = state?.ai?.leader ?? state?.ai?.default, initialRuntime = taskRuntime("leader"), initialModel = selection?.model || "", initialEffort = selection?.effort || "";
    $("backdrop").innerHTML = `<form class="modal leader-modal" id="leader-form" role="dialog" aria-modal="true" aria-labelledby="leader-title"><h3 id="leader-title">起一个 leader</h3><p class="d">它在后台运行，你在这里跟它聊。下属可以以后再收编进来，它也能自己开新下属。</p><div class="field"><label for="leader-name">叫它</label><input id="leader-name" name="name" placeholder="比如：打包" required></div><div class="field"><label for="leader-task">它要管什么</label><textarea id="leader-task" name="task" rows="3" placeholder="一两句话，比如：安卓打包和推包流程跑通，能无人值守出包" required></textarea></div><div class="field"><span class="field-label" id="leader-runtime-label">执行工具</span><input type="hidden" name="runtime" value="${esc(initialRuntime)}"><div class="model-options runtime-options" id="leader-runtime-options" role="group" aria-labelledby="leader-runtime-label"></div><p id="leader-runtime-status" role="status"></p><button type="button" class="runtime-refresh" data-act="runtimes-refresh">重新检测执行工具和模型</button></div><div class="field"><span class="field-label" id="leader-model-label">用什么模型</span><input type="hidden" name="model" value="${esc(initialModel)}"><div class="model-options" id="leader-model-options" role="group" aria-labelledby="leader-model-label"></div></div><div class="field" id="leader-effort-field"><span class="field-label" id="leader-effort-label">effort<span class="effort-note" id="leader-effort-note" hidden>此模型不支持</span></span><input type="hidden" name="effort" value="${esc(initialEffort)}"><div class="model-options effort-options" id="leader-effort-options" role="group" aria-labelledby="leader-effort-label" aria-disabled="false"></div></div><p class="error-line" id="leader-error"></p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="submit">起 leader</button></div></form>`;
    $("backdrop").classList.add("show"); renderLeaderRuntime(); $("leader-name").focus();
    refreshRuntimes();
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
    const items = state.leaders.map(item => ({group:"Leader", key:item.name + item.goal, html:`${av(item.name, 22)}${esc(item.name)}${priorityBadge(item.priority)}<span class="sm">${CrewChat.renderInline(item.goal, item.session_id)}</span>`, go:() => navigate("chat", item.session_id)}))
      .concat(state.members.map(item => ({group:"下属", key:item.task === null ? item.name : item.name + item.task, html:`${av(item.name, 22, true)}${esc(item.name)}<span class="sm">${esc(item.group)} · ${statusText[item.status]}</span>`, go:() => { goals?.closeList(); if (item.leader_session_id !== null) navigate("chat", item.leader_session_id, item.session_id); }})))
      .concat([{group:"操作", key:"起 leader 新建", html:`${ic("plus")}起 leader`, go:openModalLeader}, {group:"操作", key:"全部 架构图", html:`${ic("grid")}全部`, go:() => navigate("all")}, {group:"操作", key:"会话库 待收编 接入", html:`${ic("inbox")}会话库`, go:() => navigate("adopt")}, {group:"操作", key:"用量 额度", html:`${ic("chart")}用量`, go:() => navigate("usage")}]);
    paletteItems = items.filter(item => item.key.toLowerCase().includes(term.trim().toLowerCase()));
    let group = "";
    $("palette-results").innerHTML = paletteItems.length === 0 ? '<p class="note" style="padding:12px">没有找到匹配项</p>' : paletteItems.map((item, index) => { const title = group === item.group ? "" : `<div class="grp">${item.group}</div>`; group = item.group; return `${title}<button class="it ${index === paletteIndex ? "on" : ""}" data-act="palette-go" data-index="${index}">${item.html}</button>`; }).join("");
  }
  function syncRoute() {
    const hash = view === "chat" ? (readOnlyId === null ? `leader=${encodeURIComponent(leaderId)}` : `history=${encodeURIComponent(readOnlyId)}${leaderId === null ? "" : `&leader=${encodeURIComponent(leaderId)}`}`) + (goalId == null ? "" : `&goal=${encodeURIComponent(goalId)}`) : view;
    if (location.hash.slice(1) !== hash) history.replaceState(null, "", `${location.pathname}${location.search}#${hash}`);
  }
  function navigate(next, id = null, readonly = null, nextGoal = undefined) {
    if (nextGoal === undefined) nextGoal = next === "chat" && id === leaderId ? goalId : null;
    const sameScope = next === "chat" && view === "chat" && leaderId === id && String(goalId) === String(nextGoal);
    if (!sameScope || readOnlyId !== readonly) closeAttention();
    if (view === "chat" && (!sameScope || readOnlyId !== readonly)) { CrewChat.unmount(); mountedChat = null; }
    if (!sameScope) { goals?.closeDrawer(); rightOpen = false; }
    view = next; leaderId = id; readOnlyId = readonly; goalId = next === "chat" ? nextGoal : null;
    if (next !== "chat") { $("right-panel").classList.remove("show"); $("rp-scrim").classList.remove("show"); }
    render(); syncRoute();
    if (view === "usage" && usage === null && !usageLoading) loadUsage();
  }
  function readRoute() {
    const hash = location.hash.slice(1), params = new URLSearchParams(hash);
    goalId = params.get("goal");
    if (params.has("history")) { readOnlyId = params.get("history"); const item = member(readOnlyId); leaderId = item === undefined ? null : item.leader_session_id; view = "chat"; }
    else if (params.has("leader")) { leaderId = params.get("leader"); readOnlyId = null; view = "chat"; }
    else if (["all", "channel", "adopt", "usage", "git", "todos"].includes(hash)) { view = hash; leaderId = null; readOnlyId = null; }
    else if (hash === "" && openTodos().length > 0) { view = "todos"; leaderId = null; readOnlyId = null; }
    else if (state.leaders.length > 0) { leaderId = state.leaders[0].session_id; readOnlyId = null; view = "chat"; }
    else view = "all";
    if (view === "chat" && (readOnlyId === null ? leader(leaderId) === undefined : member(readOnlyId) === undefined)) { view = "all"; leaderId = null; readOnlyId = null; }
    if (goalId !== null) { const goal = (state.goals || []).find(item => String(item.id) === goalId); if (!goal || goal.leader_session_id !== leaderId) goalId = null; }
  }
  function render() {
    if (view !== "chat" || settingsOpen || workspaceOpen) closeAttention();
    if (settingsOpen || workspaceOpen) return;
    if (view !== "chat") goals?.sync(null, null);
    $("app").classList.toggle("wide", view !== "chat" || leaderId === null); renderSidebar();
    if (view !== "channel" && mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    if (view !== "git") CrewGit.unmount();
    if (view === "chat") renderChat(); else { if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; } if (view === "all") renderAll(); if (view === "todos") renderTodos(); if (view === "channel") renderChannel(); if (view === "adopt") renderAdopt(); if (view === "usage") renderUsage(); if (view === "git") CrewGit.mount($("main"), {project, root:state.project.root, api, toast, catalog:runtimeList, defaultRuntime:() => taskRuntime("git"), onChanged:() => loadState(true), onSettings:() => gitSettings.open(project)}); }
    renderRight(); renderCard(); document.title = `(${state.decisions.filter(item => !item.held).length}) ${projectDisplay(state.project)} · Crew`;
  }
  function watchingChat(id) {
    return !settingsOpen && !workspaceOpen && !goals?.showingList() && view === "chat" && readOnlyId === null && leaderId === id && document.visibilityState === "visible" && document.hasFocus() &&
      cardId === null && !$("backdrop").classList.contains("show") && !$("menu").classList.contains("show") &&
      !(rightOpen && window.matchMedia("(max-width:1023px)").matches);
  }
  function notifySounds(previous, fresh, requestedGeneration) {
    const decisions = state.decisions.filter(item => !item.held && !previous.decisions.some(old => old.id === item.id));
    const errors = state.attention.filter(item => item.kind === "session_error" && !previous.attention.some(old => old.id === item.id));
    const completed = new Set();
    for (const [id, waiting] of waitingSounds) {
      // An idle response requested before send succeeded cannot finish the new round.
      if (waiting.generation > requestedGeneration) continue;
      const item = leader(id) || member(id);
      if (item === undefined || !item.running || errors.some(error => error.session_id === id) || decisions.some(decision => decision.leader_session_id === id || decision.session_id === id)) waitingSounds.delete(id);
      else if (!item.busy && item.status === "idle" && item.last_seq > waiting.afterSeq) { waitingSounds.delete(id); completed.add(id); }
    }
    if (fresh) return;
    if (decisions.length > 0) CrewSound.play("decision");
    const failedChannel = notification => (notification.channels || [notification.channel]).some(channel => ["error", "rejected"].includes(channel.state));
    const failed = errors.length > 0 || failedChannel(state.notification) && !failedChannel(previous.notification) || state.notification.error !== null && state.notification.error !== previous.notification.error;
    if (failed) CrewSound.play("error");
    if (completed.size > 0) CrewSound.play("complete");
    if (decisions.length > 0 || failed || completed.size > 0) return;
    // A streamed reply keeps its message id, so only a new id counts; unread can be cleared by auto-read while unfocused.
    if (state.leaders.some(item => { const old = previous.leaders.find(old => old.session_id === item.session_id); return old !== undefined && item.last_reply_id !== null && item.last_reply_id !== old.last_reply_id && !watchingChat(item.session_id) && !waitingSounds.has(item.session_id); })) CrewSound.play("reply");
  }
  async function loadState(fresh = false) {
    if (pollBusy) { await statePromise; if (fresh) return loadState(true); return; }
    pollBusy = true;
    let complete;
    statePromise = new Promise(resolve => { complete = resolve; });
    const requestedGeneration = soundGeneration;
    try {
      const result = await api("/api/state"), previous = state; state = result; nativeApprovals?.sync(state); CrewActivity.sync(state); applyTheme(); renderSettings();
      if (project === null) { $("app").className = "workspace-entry"; $("sidebar").hidden = true; $("right-panel").hidden = true; replace($("main"), `<div class="workspace-home"><span class="workspace-logo" aria-hidden="true">${CrewTheme.logo()}</span><h1>Crew 工作区</h1><button class="btn ghost" data-act="settings">设置</button><p class="note">选一个项目，继续聊。</p><div class="list">${state.projects.map(item => `<a class="li project-entry" style="${CrewTheme.projectStyle(state.themes, item.theme)}" href="/?project=${encodeURIComponent(item.name)}"><span class="project-logo" aria-hidden="true">${CrewTheme.logo()}</span><span class="grow">${esc(projectDisplay(item))}</span><span class="project-theme-name">${esc(CrewTheme.get(state.themes, item.theme).name)}</span>${ic("right")}</a>`).join("")}</div></div>`); document.title = "工作区 · Crew"; return; }
      if (previous === null) readRoute();
      if (readOnlyId !== null && !state.members.some(item => item.session_id === readOnlyId)) { navigate("all"); toast("该会话已不在下属列表，已返回全部"); }
      else if (view === "chat" && readOnlyId === null && leader(leaderId) === undefined) { navigate("all"); toast("这个 leader 已结束，已返回全部"); }
      else if (view === "chat" && goalId != null && !(state.goals || []).some(item => String(item.id) === String(goalId) && item.leader_session_id === leaderId)) { navigate("chat",leaderId,readOnlyId,null); toast("目标已不在本组，已回到总对话。"); }
      else { render(); syncRoute(); }
      if (view === "usage" && usage === null && !usageLoading) loadUsage();
      if (previous !== null) notifySounds(previous, fresh, requestedGeneration);
    } catch (error) {
      if (state === null) {
        $("main").innerHTML = `<div class="workspace-home" role="alert"><h1>Crew 加载失败</h1><button class="btn ghost" data-act="settings">打开设置</button><p class="error-line">${esc(error.message)}</p><p class="note">每 3 秒自动重试，也可以立即重试。</p><button class="btn primary" data-act="state-retry">立即重试</button></div>`;
        delete $("main").dataset.render;
        const retry = $("main").querySelector('[data-act="state-retry"]');
        retry.onclick = async () => { retry.disabled = true; try { await loadState(); } finally { retry.disabled = false; } };
      } else toast(error.message);
    }
    finally { pollBusy = false; complete(); }
  }
  async function answer(id, text) {
    const item = state.decisions.find(item => item.id === id);
    const result = await api("/api/answer", {id, text});
    decisionsOpen.delete(id); toast(`已答复 ${item.asker}，${result.routed_to === null ? "它下一轮会收到" : `${result.routed_to} 会叫醒它`}`); await loadState(true);
  }
  async function changeSession(id, path, label, undoable) {
    const result = await api(path, {session_id:id});
    hideMenu(); if (cardId === id) closeCard(); await loadState(true); toast(label, undoable ? async () => { await api("/api/session/undo", {session_id:id, before:result.before}); await loadState(true); toast("已撤销"); } : null);
  }
  function setup() {
    document.addEventListener("compositionstart", event => {
      clearTimeout(compositionTimer); composingInput = event.target;
    });
    document.addEventListener("compositionend", () => {
      // The final input event follows compositionend. Re-render from current
      // state/drafts after it, rather than replaying HTML containing raw pinyin.
      compositionTimer = setTimeout(() => {
        composingInput = null;
        if (compositionRenderPending) { compositionRenderPending = false; render(); }
      }, 0);
    });
    $("overlay-root").innerHTML = '<button class="rp-scrim" id="rp-scrim" data-act="right-close" aria-label="关闭架构图"></button><div class="card-back" id="card-back"></div><section class="card" id="card" role="dialog" aria-modal="true" aria-label="下属卡片" tabindex="-1"></section><div class="menu" id="menu" role="menu"></div><div class="backdrop" id="backdrop"></div><div id="settings-parking" hidden><section class="settings-popover" id="settings" aria-label="外观设置"></section></div>';
    document.addEventListener("click", event => { if (event.target.closest('#chat-attention [data-file-path],#chat-attention [data-document-id]')) closeAttention(); }, true);
    $("chat-attention").addEventListener("beforetoggle", event => { if (event.newState === "open") positionAttention(); });
    $("chat-attention").addEventListener("toggle", () => {
      $("chat-head")?.querySelector('[data-act="attention"]')?.setAttribute("aria-expanded", String(attentionOpen()));
    });
    window.addEventListener("resize", () => { if (attentionOpen()) positionAttention(); });
    maintenance = window.CrewMaintenance.create({api,
      async onCleaned() { await loadState(true); if (view !== "all") navigate("all"); toast("已归档旧工作，可以重新开始"); }});
    gitSettings = CrewGitSettings.create({read:name => api(`/api/git/settings?project=${encodeURIComponent(name)}`), save:body => api("/api/git/settings", body),
      onSaved:() => { CrewGit.reload(project); void loadState(true); }, onLeader:id => navigate("chat", id),
      onDefaults:() => openSettings("ai"), onCreate:() => { navigate("git"); CrewGit.reload(project); }});
    workspaceSettings = window.CrewWorkspaceSettings.create({read:name => api(`/api/project/settings?project=${encodeURIComponent(name)}`),
      update:body => api("/api/project/update", body), remove:body => api("/api/project/remove", body),
      openCleanup:(name, label) => { void maintenance.openCleanup(name, label); },
      onSaved:() => { void loadState(true); }, onRemoved:() => location.assign("/"),
      onClose:() => { workspaceOpen = false; if (state !== null && project !== null) { render(); $("sidebar").querySelector('[data-act="workspace-settings"]')?.focus({preventScroll:true}); } }});
    settingsUI = window.CrewSettings.create({host:$("settings-surface"), read:async() => { await api("/api/runtimes?refresh=1"); return api("/api/settings"); }, save:body => api("/api/settings",body), onClose:closeSettings, service:() => location.origin,
      operations:{snapshot:() => api('/api/admin/chat'), send:body => api('/api/admin/chat/send',body),
        interrupt:() => api('/api/admin/chat/interrupt',{}), configGet:() => api('/api/admin/config'),
        configSave:body => api('/api/admin/config',body)},
      onSnapshot(value) { if (state !== null) state.ai = value.ai; runtimesCache = value.runtimes; renderLeaderRuntime(); },
      panels:{appearance(host) { host.append($("settings")); return () => $("settings-parking").append($("settings")); }, connections:settingsConnections, storage:settingsStorage}});
    try { const stored = localStorage.getItem("crew.theme"); if (["system", "light", "dark"].includes(stored)) theme = stored; } catch (error) { toast(error.message); }
    try { const stored = Number(localStorage.getItem("crew.fontScale")); if (stored >= FONT_MIN && stored <= FONT_MAX) fontScale = stored; } catch (error) { toast(error.message); }
    CrewChat.initFiles(api, toast);
    if (window.CrewNativeApprovals) nativeApprovals = window.CrewNativeApprovals.create({api,toast,refreshState:() => loadState(true),onChange() { if (!state || project === null) return; renderSidebar(); if (view === "chat") renderChat(); else if (view === "all") renderAll(); }});
    if (window.CrewDocuments && project !== null) documents = window.CrewDocuments.create({project, api, toast,
      onChange:() => goals?.renderMaterials(),
      onSource(item) {
        const owner = leader(item.leader_session_id) || leader(item.session_id) || leader(member(item.session_id)?.leader_session_id);
        if (owner) navigate("chat", owner.session_id, member(item.session_id)?.session_id || null, item.goal_id ?? goalId); else toast("来源 leader 已不在当前列表中。");
      }});
    memberUI = window.CrewMembers.create({api,toast,getLeader:id=>leader(id),globalChoice:()=>state?.ai?.member ?? null,catalog:runtimeList,
      refresh:async()=>{await refreshRuntimes();if(runtimesError)throw Error(runtimesError);},
      onChanged:async(id,parent)=>{await loadState(true);if(id){goals?.closeList();navigate("chat",parent,id,null);}}});
    if (window.CrewLeaderTrust) leaderTrust = window.CrewLeaderTrust.create({
      getLeader:id => state?.leaders.find(item => item.session_id === id), reportUrl:trustReportUrl, readReport:readTrustReport,
      renderMarkdown:(text,id) => CrewChat.renderMarkdown(text,id), save:body => api("/api/trust",body), onSaved:() => loadState(true), toast});
    if (window.CrewRuntimeSwitch) runtimeSwitch = window.CrewRuntimeSwitch.create({
      getMember:id => state?.members.concat(state?.leaders || []).find(item => item.session_id === id), catalog:runtimeList, effortsFor:modelEfforts,
      refreshCatalog:refreshRuntimes, prepare:body => api("/api/session/runtime/prepare",body),
      switchRuntime:body => api("/api/session/runtime/switch",body), onChanged:() => { goals?.invalidate(); return loadState(true); }, toast});
    if (window.CrewGoals && project !== null) goals = window.CrewGoals.create({project, api, toast, state:() => state,
      refreshState:() => loadState(true), onChange:() => { if (state && view === "chat") renderChat(); },
      renderDocuments:(items,id) => documents?.taskCards(items,id),
      onGeneral:id => navigate("chat", id, readOnlyId, null),
      onNavigate:id => { const item = (state.goals || []).find(item => String(item.id) === String(id)); if (item) { if (cardId !== null) closeCard(); navigate("chat", item.leader_session_id, null, item.id); } else toast("目标已不在当前工作区中，请刷新。"); }});
    applyTheme(); applyFontScale(); renderSettings();
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
    document.addEventListener("contextmenu", event => {
      const surface = sessionSurface(event.target);
      if (surface === null || state === null) return;
      event.preventDefault(); openSessionMenu(surface, surface.dataset.sessionMenu === undefined ? surface.dataset.id : surface.dataset.sessionMenu, {x:event.clientX, y:event.clientY});
    });
    document.addEventListener("pointerover", event => {
      const button = event.target.closest('#menu [data-act="submenu"]');
      if (button !== null && button.getAttribute('aria-expanded') !== 'true') openSubmenu(button);
    });
    document.addEventListener("click", event => {
      if (event.target.id === "backdrop") { closeBackdrop(); return; }
      if (event.target.id === "card-back") { closeCard(); return; }
      const button = event.target.closest("[data-act]");
      if (!event.target.closest("#menu") && !event.target.closest('[data-act$="-menu"]')) hideMenu();
      if (button === null || button.disabled) return;
      const act = button.dataset.act === "session-open" ? button.dataset.openAct : button.dataset.act, id = button.dataset.id;
      if (act === "settings") { openSettings(); return; }
      if (act === "git-settings") { hideMenu(); gitSettings.open(project); return; }
      if (act === "workspace-settings") { openWorkspaceSettings(); return; }
      if (act === "confirm-stop-person") { const resolve = stopConfirmation; stopConfirmation = null; closeBackdrop(); resolve?.(true); return; }
      if (act === "submenu") { openSubmenu(button); return; }
      if (button.closest("#menu")) hideMenu(["member-create", "member-defaults", "session-rename"].includes(act));
      if (act === "session-menu") openSessionMenu(button, id);
      else if (act === "member-add-menu") showMenu(button,`<button role="menuitem" data-act="member-create" data-id="${esc(id)}">${ic("plus","sm")}新建组员</button><button role="menuitem" data-act="member-adopt" data-id="${esc(id)}">${ic("inbox","sm")}收编已有会话</button>`);
      else if (act === "member-create") { hideMenu(); memberUI.openCreate(id); }
      else if (act === "member-defaults") { hideMenu(); memberUI.openDefaults(id); }
      else if (act === "member-adopt") { hideMenu();adoptLeaderId=id;navigate("adopt"); }
      else if (act === "session-rename") openRename(id);
      else if (act === "group-tree") { if (cardId !== null) closeCard(); navigate("chat", id); rightOpen = true; renderRight(); }
      else if (act === "set-priority") action("leader-order", () => setPriority(id, Number(button.dataset.rank)));
      else if (act === "leader-up" || act === "leader-down") action("leader-order", async () => {
        const item = leader(id), order = sortableLeaders(item).map(item => item.session_id), before = order.slice(), index = order.indexOf(id), other = index + (act === "leader-up" ? -1 : 1);
        [order[index], order[other]] = [order[other], order[index]];
        if (item.priority === null) await saveNormalOrder(order);
        else await savePriority(order, before);
      });
      else if (act === "close-group") action(`group:${id}`, async () => {
        const group = leader(id).name, result = await api("/api/group/close", {group, with_members:button.dataset.withMembers === "true"});
        if (cardId !== null) closeCard(); await loadState(true);
        toast("已结束 leader", async () => { await api("/api/group/undo", {group, before:result.before}); await loadState(true); toast("已撤销"); });
      });
      else if (act === "wake-session") action(`wake:${id}`, async () => { await api("/api/wake", {session_id:id, text:"用户叫醒了你，请继续当前任务。"}); await loadState(true); toast("已叫醒"); });
      else if (act === "session-handoff") action(`continue:${id}`, async () => { await api("/api/open/continue", {session_id:id, confirm:handoff.needs_confirmation}); handoff = null; closeBackdrop(); await loadState(true); toast("已在 VS Code 接上同一个会话"); });
      else if (act === "leader") { if (cardId !== null) closeCard(); goals?.closeList(); navigate("chat", id); }
      else if (act === "active-goal") { const target = (state.goals || []).find(goal => String(goal.id) === button.dataset.goalId); if (target) { goals?.closeList(); navigate("chat",target.leader_session_id,id === target.leader_session_id ? null : id,target.id); } }
      else if (act === "member-chat") { const item = member(id); if (item) { if (cardId !== null) closeCard(); goals?.closeList(); navigate("chat", item.leader_session_id, item.session_id); } }
      else if (act === "view") { if (cardId !== null) closeCard(); navigate(button.dataset.view); }
      else if (act === "todo-toggle") { if (todoExpanded.has(id)) todoExpanded.delete(id); else todoExpanded.add(id); renderTodoSurface(); }
      else if (act === "todo-action") {
        const item = todoById(id);
        if (item === undefined) return;
        const previous = todoActions.get(id);
        todoActions.set(id, {status:button.dataset.status, note:previous?.note || "", version:item.version, error:null});
        todoExpanded.add(id); renderTodoSurface(); $("todo-note-" + id)?.focus();
      }
      else if (act === "todo-action-close") { todoActions.delete(id); renderTodoSurface(); }
      else if (act === "todo-ask") {
        const item = todoById(id), owner = item && state.leaders.find(person => person.session_id === item.leader_session_id || person.session_id === item.session_id);
        if (owner) navigate("chat", owner.session_id);
      }
      else if (act === "all-tab") { allTab = button.dataset.tab; renderAll(); }
      else if (act === "card") openCard(id);
      else if (act === "card-close") closeCard();
      else if (act === "holds") { const context = button.dataset.context; if (heldExpanded.has(context)) heldExpanded.delete(context); else heldExpanded.add(context); if (view === "chat") renderChat(); else renderAll(); }
      else if (act === "decision-toggle") { const key = Number(id); if (decisionsOpen.has(key)) decisionsOpen.delete(key); else decisionsOpen.add(key); if (view === "chat") renderChat(); else renderAll(); }
      else if (act === "decision-clarify") action(`clarify:${id}`, async () => { button.disabled = true; try { await api("/api/decision/clarify", {id:Number(id)}); await loadState(true); toast("已请 leader 补充说明"); } finally { button.disabled = false; } });
      else if (act === "decision-discuss" || act === "decision-ready") action(`discuss:${id}`, async () => {
        const item = state.decisions.find(item => String(item.id) === id);
        const response = await api(act === "decision-discuss" ? "/api/decision/discuss" : "/api/decision/ready", {id:Number(id)}); await loadState(true);
        const owner = item && (leader(item.leader_session_id) || leader(item.session_id) || leader(member(item.session_id)?.leader_session_id));
        const discussionGoal = Object.prototype.hasOwnProperty.call(response,"conversation_goal_id") ? response.conversation_goal_id : item?.goal_id || null;
        if (act === "decision-discuss" && owner) navigate("chat", owner.session_id, null, discussionGoal);
      });
      else if (act === "decision-hold" || act === "decision-unhold") action(`hold:${id}`, async () => { const held = act === "decision-hold"; await api(`/api/decision/${held ? "hold" : "unhold"}`, {id:Number(id)}); await loadState(true); toast(held ? "已挂起，暂时不拍" : "已取消挂起，放回待拍板"); });
      else if (act === "answer-option") action(`answer:${id}`, () => answer(Number(id), state.decisions.find(item => item.id === Number(id)).options[Number(button.dataset.option)]));
      else if (act === "away") action("away", async () => { await api("/api/notifications/away", {away:!state.notification.manual_away}); await loadState(true); });
      else if (act === "theme") { theme = {system:"light", light:"dark", dark:"system"}[theme]; applyTheme(); try { localStorage.setItem("crew.theme", theme); } catch (error) { toast(error.message); } renderSidebar(); }
      else if (act === "theme-select") { theme = button.dataset.themeMode; applyTheme(); try { localStorage.setItem("crew.theme", theme); } catch (error) { toast(error.message); } renderSettings(); if (state !== null && project !== null) renderSidebar(); }
      else if (act === "project-theme") action("project-theme", async () => { const result = await api("/api/project/theme", {project:state.project.name, theme:button.dataset.themeId}); state.project.theme = result.theme; state.projects.find(item => item.name === state.project.name).theme = result.theme; applyTheme(); renderSettings(); renderSidebar(); await loadState(true); });
      else if (act === "leader-trust") leaderTrust?.open(id);
      else if (act === "leader-trust-report") leaderTrust?.open(id, true);
      else if (act === "runtime-switch") runtimeSwitch?.open(id);
      else if (act === "comments") openComments(id);
      else if (act === "comments-refresh") action(`comments:${id}`, () => loadComments(id));
      else if (act === "font-reset") { setFontScale(1); renderSettings(); }
      else if (act === "sound-mute") { CrewSound.setMuted(!CrewSound.getSettings().muted); renderSettings(); }
      else if (act === "sound-test") CrewSound.preview("decision");
      else if (act === "right-open") { rightOpen = true; renderRight(); $("right-panel").querySelector('[data-act="right-close"]').focus(); }
      else if (act === "right-close") { rightOpen = false; renderRight(); $("main").querySelector('[data-act="right-open"]')?.focus(); }
      else if (act === "modal-leader") openModalLeader();
      else if (act === "leader-runtime" || act === "leader-model" || act === "leader-effort") {
        const form = $("leader-form"), field = {"leader-runtime":"runtime", "leader-model":"model", "leader-effort":"effort"}[act];
        selectLeaderOption(form,field,button.dataset[field]);
        $("leader-error").textContent = "";
        renderLeaderRuntime();
        form.querySelector(`[data-act="${act}"][data-${field}="${CSS.escape(form.elements[field].value)}"]`)?.focus();
      }
      else if (act === "runtimes-refresh") action("runtimes", async () => {
        await refreshRuntimes();
        if ($("leader-form") === null && id) showMenu(menuAnchor || button, modelMenu(sessionItem(id)));
        if (runtimesError !== null && $("leader-form") === null) toast(runtimesError);
      });
      else if (act === "model-menu") {
        const refreshing = refreshRuntimes();
        showMenu(button, modelControls(sessionItem(id)));
        refreshing.then(() => { if (menuAnchor === button && $("menu").classList.contains("show")) showMenu(button, modelControls(sessionItem(id))); });
      }
      else if (act === "context-compact") action(`compact:${id}`, () => compactContext(id));
      else if (act === "compact") openCompact(id);
      else if (act === "compact-department") { const item = leader(id); if (item) void maintenance.openDepartment(project, item); }
      else if (act === "compact-confirm") action(`compact:${id}`, async () => {
        button.disabled = true;
        closeBackdrop();
        const closeStream = await CrewChat.watchCompact(id, {state, api, toast});
        try { await api("/api/session/compact", {session_id:id}); await loadState(true); }
        catch (error) { closeStream(); throw error; }
      });
      else if (act === "set-model" || act === "set-effort") action(`options:${id}`, async () => {
        const field = act === "set-model" ? "model" : "effort", value = button.dataset[field] === "" ? null : button.dataset[field];
        const item = sessionItem(id), changes = optionChange(item, field, value);
        hideMenu(); await api("/api/session/options", {session_id:id, ...changes}); await loadState(true);
        toast(`下一步起用 ${field === "model" ? value === null ? "默认" : modelName(value, item) : value}${changes.effort !== undefined && changes.effort !== null ? ` · ${changes.effort}` : ""}`);
      });
      else if (act === "modal-close") closeBackdrop();
      else if (act === "palette") openPalette();
      else if (act === "palette-go") { const item = paletteItems[Number(button.dataset.index)]; closeBackdrop(); item.go(); }
      else if (act === "usage-period") { if (period === button.dataset.period) return; period = button.dataset.period; usage = null; renderUsage(); loadUsage(); }
      else if (act === "usage-tab") { usageTab = button.dataset.tab; renderUsage(); }
      else if (act === "usage-fold") { if (usageCollapsed.has(id)) usageCollapsed.delete(id); else usageCollapsed.add(id); renderUsage(); }
      else if (act === "rename") openRename(cardId);
      else if (act === "move-menu") { const item = member(id); showMenu(button, state.leaders.filter(group => group.session_id !== item.leader_session_id).map(group => `<button data-act="move-to" data-id="${esc(id)}" data-group="${esc(group.name)}">${av(group.name, 20)}${esc(group.name)}</button>`).join("")); }
      else if (act === "more-menu") showMenu(button, `<button data-act="leave-group" data-id="${esc(id)}">${ic("leave", "sm")}退组</button><button class="danger" data-act="remove-session" data-id="${esc(id)}">${ic("x", "sm")}移出 Crew</button>`);
      else if (act === "move-to") action("move", async () => { const item = member(id); await api("/api/roster/move", {session_id:item.session_id, name:item.name, from:item.group, to:button.dataset.group}); hideMenu(); await loadState(true); toast(`已换到 ${button.dataset.group} 组`); });
      else if (act === "leave-group") action("leave", async () => { const item = member(id); await api("/api/roster/remove", {session_id:item.session_id, name:item.name, group:item.group}); hideMenu(); closeCard(); await loadState(true); toast("已退组，可以在待收编里重新收编"); });
      else if (act === "archive") action("archive", () => changeSession(id, "/api/session/archive", "已归档", true));
      else if (act === "remove-session") action("remove", () => changeSession(id, "/api/session/remove", "已移出 Crew", true));
      else if (act === "stop") action("stop", async () => { await api("/api/session/stop", {session_id:id}); await loadState(true); toast("已请求中断本轮，正在等待停止确认"); });
      else if (act === "open-vscode") action(`open:${id}`, async () => {
        const reason = editorHandoffReason(sessionItem(id));
        if (reason) throw new Error(reason);
        const result = await api("/api/open", {session_id:id});
        if (result.needs_window_ready) { if (cardId === id) { handoff = {...result, session_id:id}; renderCard(); } else openHandoff(id, result); }
        else toast("已在 VS Code 打开");
      });
      else if (act === "handoff-cancel") { handoff = null; renderCard(); }
      else if (act === "handoff-go") action("continue", async () => { await api("/api/open/continue", {session_id:id, confirm:handoff.needs_confirmation}); handoff = null; await loadState(true); toast("已在 VS Code 接上同一个会话"); });
      else if (act === "member-history") { const item = member(id); if (cardId !== null) closeCard(); navigate("chat", item.leader_session_id, item.session_id); }
    });
    document.addEventListener("input", event => {
      if (event.target.closest('#session-rename-form') && event.target.name === 'name') {
        const form=event.target.form;form.querySelector('[type=submit]').disabled = !event.target.value.trim() || event.target.value.trim() === form.dataset.original;
        $("rename-error").textContent = "";
      }
      const commentForm = event.target.closest("#comments-form");
      if (commentForm && event.target.name === "text") { sessionComments(commentForm.dataset.sessionId).draft = event.target.value; return; }
      const todoForm = event.target.closest("[data-todo-form]");
      if (todoForm && event.target.name === "note") { const draft = todoActions.get(todoForm.dataset.todoForm); if (draft) draft.note = event.target.value; return; }
      // Update only the readout while dragging; re-rendering would steal the slider from the pointer.
      if (event.target.dataset.setting === "font") { setFontScale(Number(event.target.value)); $("font-scale-value").textContent = `${Math.round(fontScale * 100)}%`; $("settings").querySelector('[data-act="font-reset"]').disabled = fontScale === 1; return; }
      if (event.target.dataset.setting === "volume") { CrewSound.setVolume(Number(event.target.value)); $("sound-volume-value").textContent = `${Math.round(Number(event.target.value) * 100)}%`; return; } });
    document.addEventListener("submit", event => {
      const form = event.target;
      if (form.id === "comments-form") { event.preventDefault(); action(`comment-post:${form.dataset.sessionId}`, () => postComment(form.dataset.sessionId, form.elements.text.value)); }
      else if (form.dataset.todoForm !== undefined) { event.preventDefault(); const id = form.dataset.todoForm, draft = todoActions.get(id); if (draft) draft.note = form.elements.note.value; action(`todo:${id}`, () => resolveTodo(id)); }
      else if (form.dataset.answerForm !== undefined) { event.preventDefault(); const text = form.elements.answer.value.trim(); if (text !== "") action(`answer:${form.dataset.answerForm}`, () => answer(Number(form.dataset.answerForm), text)); }
      else if (form.id === "session-rename-form") { event.preventDefault(); void saveRename(form); }
      else if (form.id === "leader-form") { event.preventDefault(); action("leader", async () => { const button = form.querySelector('[type="submit"]'); button.disabled = true; try { const result = await api("/api/leader", leaderPayload(form)); closeBackdrop(); await loadState(true); navigate("chat", result.session_id); } catch (error) { if ($("leader-error")) $("leader-error").textContent = error.message; } finally { button.disabled = !runtimeInfo(form.elements.runtime.value).available; } }); }
    });
    document.addEventListener("toggle", event => {
      const details = event.target;
      if (details.dataset.decisionContext === undefined) return;
      const id = Number(details.dataset.decisionContext);
      if (details.open) decisionsOpen.add(id); else decisionsOpen.delete(id);
    }, true);
    document.addEventListener("keydown", event => {
      if (workspaceOpen) return;
      if (event.key === "Escape" && attentionOpen() && !event.isComposing) { event.preventDefault(); closeAttention(true); return; }
      if (event.key === "ContextMenu" || event.shiftKey && event.key === "F10") {
        const surface = sessionSurface(event.target);
        if (surface !== null) { event.preventDefault(); openSessionMenu(surface, surface.dataset.sessionMenu === undefined ? surface.dataset.id : surface.dataset.sessionMenu); return; }
      }
      if ($("menu").classList.contains("show") && ["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Escape", "Home", "End", "Tab"].includes(event.key)) {
        event.preventDefault();
        const current = document.activeElement, menu = current.closest('[role="menu"]'), items = menuItems(menu), index = items.indexOf(current);
        if (event.key === "Tab") hideMenu(true);
        else if (event.key === "ArrowRight" && current.dataset.act === "submenu") openSubmenu(current);
        else if (event.key === "ArrowLeft" || event.key === "Escape") {
          if (menu === $("menu")) hideMenu(true);
          else { const parent = menu.previousElementSibling; closeSubmenu(menu.parentElement.parentElement); parent.focus(); }
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && state !== null && project !== null) { event.preventDefault(); if (stopConfirmation === null) openPalette(); return; }
      if (event.key === "Escape" && settingsOpen) { closeSettings(); return; }
      if (settingsOpen) return;
      if (event.key === "Escape") { if ($("menu").classList.contains("show")) { hideMenu(); return; } if ($("backdrop").classList.contains("show")) closeBackdrop(); else if (cardId !== null) closeCard(); else if (goals?.escape()) return; else if (rightOpen) { rightOpen = false; renderRight(); } }
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
