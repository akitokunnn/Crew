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
  let documents = null, leaderTrust = null, runtimeSwitch = null, goals = null, goalId = null;
  let allTab = "tree";
  const operationIds = new Set();
  let people = null, leaderOrdering = null, usageUI = null, menus = null, appearance = null, waitingNotifications = null, palette = null;
  let conversationWorkspace = null, uiActions = null, contextCompaction = null, attentionFeature = null, commentsFeature = null, runtimeCatalog = null, runtimeModels = null, leaderCreate = null, uiVisibility = null, domRenderer = null;
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
  const time = at => new Date(at).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false});
  const date = at => new Date(at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  function ago(at) { const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000); return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`; }
  const leader = id => state.leaders.find(item => item.session_id === id);
  const member = id => state.members.find(item => item.session_id === id);
  const workingPeople = people => people.filter(item => item.status === "working");
  const workingDispatches = people => people.flatMap(person => person.codex.filter(task => task.source === "skill_dispatch" && task.status === "running").map(task => ({person, task})));
  const runtimeBadge = (item, compact = false) => `<span class="runtime-tag ${compact ? "compact" : ""}" data-runtime="${esc(runtimeCatalog.id(item))}" title="执行工具：${esc(runtimeCatalog.info(item).name)}">${esc(compact && runtimeCatalog.id(item) === "claude" ? "CC" : runtimeCatalog.info(item).name)}</span>`;
  const memberModel = item => item.where === "bg" ? `<span class="member-model">${esc(runtimeCatalog.optionsLabel(item))}</span>` : "";
  const memberActivity = item => item.where === "bg" ? CrewActivity.markup(item, true) : "";
  const contextRing = item => CrewContext.ring(item.context, {id:item.session_id, name:item.name, busy:item.busy,
    external:item.where === "vscode", phase:contextCompaction?.phase(item.session_id) || (item.activity?.phase === "compacting" ? "compacting" : null)});
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
  function queryPath(path) { const url = new URL(path, location.origin); if (project !== null && url.pathname !== "/api/settings") url.searchParams.set("project", project); return url.pathname + url.search; }
  async function api(path, data) {
    const sentAfter = path === "/api/chat/send" ? (leader(data.session_id) || member(data.session_id))?.last_seq || 0 : null;
    const response = await fetch(queryPath(path), data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Crew":"1"}, body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || "请求失败，请重试。"); error.status = response.status; error.details = result; throw error; }
    if (path === "/api/chat/send" && data.goal_id == null) waitingNotifications?.acceptedSend({sessionId:data.session_id,afterSeq:sentAfter});
    else if (["/api/session/stop", "/api/session/archive", "/api/session/remove"].includes(path)) waitingNotifications?.acceptedStop(data.session_id);
    return result;
  }
  function toast(message, undo = null) {
    const node = document.createElement("div"); node.className = "toast show"; node.setAttribute("role", "status"); node.textContent = message;
    if (undo !== null) { const button = document.createElement("button"); button.type = "button"; button.textContent = "撤销"; button.onclick = async () => { button.disabled = true; try { await undo(); node.remove(); } catch (error) { button.disabled = false; toast(error.message); } }; node.append(button); }
    $("toast-root").replaceChildren(node); setTimeout(() => node.remove(), undo === null ? 5000 : 12000);
  }
  async function action(key, fn) { if (operationIds.has(key)) return; operationIds.add(key); try { await fn(); } catch (error) { toast(error.message); } finally { operationIds.delete(key); } }
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
  function notificationChannel() {
    const channels = state.notification.channels || [{...state.notification.channel, id:"qq", name:"QQ"}];
    const labels = {unconfigured:"未配置", disabled:"已断开", disconnected:"已断开", unbound:"等待绑定", connecting:"连接中", connected:"已连接", reconnecting:"重连中", rejected:"被拒收", error:"出错", stopped:"已停止"};
    return channels.map(channel => {
      const hint = channel.state === "unbound" ? "在设置的消息渠道中复制绑定命令，私聊机器人完成绑定" : channel.state === "rejected" && channel.id === "qq" ? "在手机 QQ 打开允许主动发送" : channel.detail;
      return `<div class="qq-status ${esc(channel.state)}" role="status"${hint ? ` title="${esc(hint)}"` : ""}><div class="qq-state"><i></i>${esc(channel.name)} · ${esc(labels[channel.state] || channel.state)}</div>${hint ? `<div class="qq-detail">${esc(hint)}</div>` : ""}</div>`;
    }).join("");
  }
  const priorityAttrs = item => item.priority === null ? "" : `data-level="${item.priority}"`;
  const priorityBadge = (rank, corner = false) => rank === null ? "" : `<span class="priority-mark ${corner ? "corner" : ""}" data-level="${rank}" title="第 ${rank} 优先"><span class="rank">${rank}</span></span>`;
  function sidebarLeader(item) {
    const selected = view === "chat" && leaderId === item.session_id && readOnlyId === null;
    const approvals = nativeApprovals?.countForLeader(item.session_id) || 0;
    const work = groupWork(item);
    const label = [item.name, item.priority === null ? "" : `第 ${item.priority} 优先`, work.label, item.unread > 0 ? `${item.unread} 条未读` : "", item.pending > 0 ? `${item.pending} 件待拍板` : "", approvals ? `${approvals} 项执行权限待确认` : ""].filter(Boolean).join("，");
    return `<div draggable="true" class="leader-item session-surface ${item.priority === null ? "" : "pinned"} ${selected ? "active" : ""}" data-act="leader" data-id="${esc(item.session_id)}" title="${esc(label)}" aria-label="${esc(label)}" ${selected ? 'aria-current="page"' : ""}>${sessionOpen(item.session_id, "leader", label, selected)}${groupAvatar(item, work)}<span class="meta"><span class="nm"><span class="leader-name">${esc(item.name)}</span>${item.priority === null ? "" : `<span class="leader-priority" title="第 ${item.priority} 优先">优先 ${item.priority}</span>`}</span><span class="goal">${CrewChat.renderInline(item.goal, item.session_id)}</span></span><span class="leader-details">${runtimeBadge(item, true)}${item.is_git_admin ? '<span class="tag git-admin-tag">Git</span>' : ""}${contextRing(item)}${groupWorkLabel(work, true)}<span class="badges">${approvals ? `<span class="native-approval-count" title="${approvals} 项执行权限待确认">权限 ${approvals}</span>` : ""}${item.pending > 0 ? `<span class="b-pending" title="${item.pending} 件等你拍板">${item.pending}</span>` : ""}${item.unread > 0 ? `<span class="b-unread" title="${item.unread} 条未读">${item.unread}</span>` : ""}</span></span></div>`;
  }
  function renderSidebar() {
    if (leaderOrdering?.isInteracting()) return;
    const themeIcon = {system:"auto", light:"sun", dark:"moon"}[appearance.mode()], themeName = {system:"跟随系统", light:"浅色", dark:"深色"}[appearance.mode()];
    domRenderer.replace($("sidebar"), `<div class="brand"><span class="brand-logo" aria-hidden="true">${CrewTheme.logo()}</span><div class="brand-copy"><div class="proj" title="${esc(projectDisplay(state.project))}">${esc(projectDisplay(state.project))}</div><div class="sub">Crew 工作区</div></div><button class="icon-btn workspace-settings-entry" data-act="workspace-settings" title="当前工作区设置" aria-label="当前工作区设置">${ic("settings", "sm")}</button></div>
      <button class="search-btn" data-act="palette" title="找 leader 或下属 · Ctrl K">${ic("search", "sm")}<span class="search-label">找 leader 或下属</span><span class="kbd">Ctrl K</span></button>
      <button class="nav-item ${view === "all" ? "active" : ""}" data-act="view" data-view="all">${ic("grid")}<span>全部</span>${nativeApprovals?.count() ? `<span class="native-approval-count" title="执行权限待确认">权限 ${nativeApprovals.count()}</span>` : ""}</button>
      <button class="nav-item ${view === "channel" ? "active" : ""}" data-act="view" data-view="channel">${ic("msg")}<span>交流区</span>${state.channel.unread > 0 ? `<span class="b-unread channel-unread" title="交流区未读">${state.channel.unread}</span>` : ""}</button>
      <button class="nav-item ${view === "git" ? "active" : ""}" data-act="view" data-view="git">${ic("branch")}<span>Git</span></button>
      <button class="nav-item ${view === "todos" ? "active" : ""}" data-act="view" data-view="todos">${ic("check")}<span>我的待办</span>${attentionFeature.openTodos().length > 0 ? `<span class="todo-count">${attentionFeature.openTodos().length}</span>` : ""}</button>
      <div class="sb-leaders"><section class="priority-zone ${leaderOrdering.ids().length === 0 ? "empty-priority" : ""}" data-leader-zone="priority"><div class="sec-label">置顶 · 优先</div>${leaderOrdering.priority().map(sidebarLeader).join("")}<div class="priority-drop-hint">拖到这里设为优先</div></section>
      <section class="leader-list" data-leader-zone="normal"><div class="sec-label">Leader</div>${state.leaders.filter(item => item.priority === null).map(sidebarLeader).join("")}
      <button class="leader-item add-leader" data-act="modal-leader"><span class="plus">${ic("plus", "sm")}</span><span>起 leader</span></button></section></div>
      <div class="sb-mid"><button class="nav-item ${view === "adopt" ? "active" : ""}" data-act="view" data-view="adopt">${ic("inbox")}<span>会话库</span></button><button class="nav-item ${view === "usage" ? "active" : ""}" data-act="view" data-view="usage">${ic("chart")}<span>用量</span></button></div>
      <div class="sb-foot">${usageUI.summaryMarkup()}${notificationChannel()}<div class="foot-row"><button class="switch ${state.notification.manual_away ? "on" : ""}" data-act="away" role="switch" aria-checked="${state.notification.manual_away}"><span class="track"></span><span>我已离开</span></button><span class="gap"></span><button class="icon-btn" data-act="settings" title="设置" aria-label="设置">${ic("settings")}</button><button class="icon-btn" data-act="theme" title="${themeName}" aria-label="主题：${themeName}">${ic(themeIcon)}</button></div>${state.notification.away ? '<div class="away-note">离开中：待拍板会发到已连接的消息渠道</div>' : ""}${state.notification.error === null ? "" : `<div class="error-line">${esc(state.notification.error)}</div>`}</div>`);
  }
  function codexTaskName(item, sessionId) {
    if (item.source !== "native_subagent") return CrewChat.renderInline(item.task_name, sessionId);
    const name = Array.from(String(item.task_name || "子代理").replace(/\s+/g, " ").trim());
    return esc(name.length > 48 ? name.slice(0, 47).join("") + "…" : name.join(""));
  }
  function codexMarkup(item, cls = "cx", sessionId) {
    const running = item.status === "running";
    const label = ({running:"运行中",succeeded:"已完成",failed:"失败",interrupted:"已暂停",closed:"已关闭",unknown:"状态未确认"})[item.status] || "状态未确认";
    const icon = item.status === "succeeded" ? "check" : item.status === "failed" ? "x" : item.status === "interrupted" ? "pause" : item.status === "closed" ? "stop" : "more";
    const hint = `${item.runtime === "claude" ? "Claude" : "Codex"} ${item.source === "native_subagent" ? "子代理" : "派活"} · ${label}${item.pid ? " · PID " + item.pid : ""}${item.cwd ? " · " + item.cwd : ""}${item.detail ? " · " + item.detail : ""}`;
    return `<div class="${cls} codex-status-${esc(item.status)}" data-codex-source="${esc(item.source || "dispatch")}" title="${esc(hint)}">${ic("bolt", "xs")}${item.source === "skill_dispatch" ? runtimeBadge(item, true) : ""}<span class="${cls === "cx" ? "n" : "grow"}" title="${esc(item.task_description || item.task_name)}">${codexTaskName(item, sessionId)}</span><span class="s" aria-label="${esc(label)}">${running ? `<span class="spin" style="width:10px;height:10px"></span>${Math.max(0, Math.floor((Date.now() - new Date(item.started_at)) / 60000))} 分钟` : `${ic(icon, item.status === "succeeded" ? "xs ok" : "xs")}${item.status === "succeeded" && item.ended_at ? time(item.ended_at) : esc(label)}`}</span></div>`;
  }
  const codexList = item => item.codex.length > 0 || item.codex_older > 0 ? `<span class="cx-list">${item.codex.map(task => codexMarkup(task, "cx", item.session_id)).join("")}${item.codex_older > 0 ? `<span class="cx older">${ic("more", "xs")}<span class="n">更早 ${item.codex_older} 个</span></span>` : ""}</span>` : "";
  function memberNode(item) {
    const where = item.where === "bg" ? "cloud" : item.where === "vscode" ? "code" : "clock";
    return `<div class="session-node"><div class="node session-surface click ${readOnlyId === item.session_id ? "sel" : ""}" data-act="member-chat" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}">${sessionOpen(item.session_id, "member-chat", `和「${item.name}」对话`)}<span class="node-top">${av(item.name, 26, true)}<span class="nm">${esc(item.name)}${runtimeBadge(item, true)}${contextRing(item)}<span class="where" title="${item.where === "bg" ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}">${ic(where, "xs")}</span></span>${st(item.status)}</span>${memberModel(item)}<span class="now">${item.task === null ? "还没写" : CrewChat.renderInline(item.task, item.session_id)}</span>${memberActivity(item)}${codexList(item)}</div><div class="session-node-actions">${commentsFeature.button(item)}<button class="session-comment" data-act="card" data-id="${esc(item.session_id)}">资料与操作</button></div></div>`;
  }
  function leaderNode(item, clickable) {
    const work = groupWork(item);
    return `<div class="session-node leader-unit"><${clickable ? "button" : "div"} class="node ${clickable ? "click" : ""} leader-node ${item.priority === null ? "" : "priority"}" ${priorityAttrs(item)} ${clickable ? `data-act="leader" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}"` : ""}>${priorityBadge(item.priority, true)}<span class="node-top">${groupAvatar(item, work)}<span class="nm">${esc(item.name)}<span class="tag" style="background:var(--hover);color:var(--text-3)">leader</span>${runtimeBadge(item)}</span>${item.pending > 0 ? `<span class="b-pending" style="margin-left:auto">${item.pending}</span>` : st(item.status)}</span><span class="leader-work-summary">${groupWorkLabel(work)}</span><span class="now" style="margin-left:39px">${CrewChat.renderInline(item.goal, item.session_id)}</span>${codexList(item)}</${clickable ? "button" : "div"}><div class="session-node-actions">${commentsFeature.button(item, true)}</div></div>`;
  }
  function workingRow(item) {
    const isLeader = leader(item.session_id) !== undefined;
    const task = isLeader ? item.goal : item.task;
    return `<div class="li click session-surface" data-act="${isLeader ? "leader" : "member-chat"}" data-session-menu="${esc(item.session_id)}" data-id="${esc(item.session_id)}">${sessionOpen(item.session_id, isLeader ? "leader" : "member-chat", `和「${item.name}」对话`)}${av(item.name, 32, !isLeader)}<span class="grow"><span class="ttl">${esc(item.name)}${runtimeBadge(item, true)}${contextRing(item)} <span class="where">${ic(item.where === "bg" ? "cloud" : "code", "xs")}</span><span class="li-sub">${isLeader ? "leader" : `归 ${esc(item.group)}`}</span>${memberModel(item)}</span><span class="li-now">${task === null ? "还没写" : CrewChat.renderInline(task, item.session_id)}</span>${memberActivity(item)}<span class="sm">${item.codex.filter(task => task.status === "running").map(task => `<span>${ic("bolt", "xs")} ${codexTaskName(task, item.session_id)} · <span class="spin" style="width:9px;height:9px;display:inline-block"></span> ${Math.floor((Date.now() - new Date(task.started_at)) / 60000)} 分钟</span>`).join("")}</span></span>${st(item.status)}</div>`;
  }
  function workingDispatchRow({person, task}) {
    const isLeader = leader(person.session_id) !== undefined;
    return `<button class="li click" data-act="${isLeader ? "leader" : "member-chat"}" data-id="${esc(person.session_id)}" data-dispatch-id="${esc(task.id)}" title="查看发起人对话 · PID ${esc(task.pid)}"><span class="av" style="--s:32px;--tint:var(--working)">${ic("bolt", "sm")}</span><span class="grow"><span class="ttl">${CrewChat.renderInline(task.task_name, person.session_id)}${runtimeBadge(task, true)}</span><span class="li-sub">临时派工 · 由 ${esc(person.name)} 派出</span></span><span class="li-now">PID ${esc(task.pid)}</span>${st("working")}</button>`;
  }
  function renderRight() {
    if (view !== "chat" || leaderId === null) { $("right-panel").innerHTML = ""; delete $("right-panel").dataset.render; return; }
    const item = leader(leaderId), members = membersOf(leaderId);
    domRenderer.replace($("right-panel"), `<div class="head"><div><div class="title" title="${esc(item.name)} 组" style="font-size:calc(13px * var(--fs))">本组架构图</div><div class="sub">${members.length} 个下属</div><button class="member-default-entry" data-act="member-defaults" data-id="${esc(item.session_id)}">组员大脑 · ${item.member_defaults || state.ai?.member ? esc((item.member_defaults ?? state.ai.member).runtime ? runtimeCatalog.info((item.member_defaults ?? state.ai.member).runtime).name : "自动选择") : "跟随 leader"} ›</button></div><div class="right"><button class="btn ghost" data-act="member-add-menu" data-id="${esc(item.session_id)}" style="padding:5px 9px;font-size:calc(12.5px * var(--fs))">${ic("plus", "sm")}添加</button><button class="icon-btn rp-close" data-act="right-close" aria-label="关闭架构图">${ic("x")}</button></div></div><div class="tree">${leaderNode(item,true)}<div class="members">${members.map(memberNode).join("")}</div><div class="rp-note">点任意人进入本人对话；资料与操作或右键可查看卡片。${ic("code", "xs")} VS Code，${ic("cloud", "xs")} Crew 后台。</div></div>`);
    syncWorkspace();
  }
  function closeTaskTools() { conversationWorkspace?.close("tasks"); }
  function closeAuxiliary(restoreFocus = false) { conversationWorkspace?.closeAuxiliary(restoreFocus); }
  function syncWorkspace() {
    conversationWorkspace?.update({
      active:view === "chat" && (leaderId !== null || readOnlyId !== null) && !settingsOpen && !workspaceOpen,
      scope:view === "chat" ? `${readOnlyId ?? leaderId}:${goalId ?? ""}` : null,
      listing:goals?.showingList() === true, task:goalId != null, materialsOpen:goals?.materialsOpen() === true
    });
  }
  function renderChat() {
    const item = readOnlyId === null ? leader(leaderId) : member(readOnlyId), group = leader(leaderId), members = membersOf(leaderId);
    const work = group === undefined ? null : groupWork(group);
    const attention = attentionFeature.forChat({ownerId:leaderId, goalId, member:readOnlyId !== null});
    const {pending, held, todos:groupTodos} = attention;
    goals?.sync(leaderId, goalId);
    const listing = goals?.showingList() === true;
    const access = chatAccess(item, goalId == null ? null : goals?.currentGoal());
    const execution = goalId == null ? null : goals?.executionFor(item.session_id);
    const unstarted = goalId == null || execution ? null : goalChatNotes(goals?.currentGoal(), readOnlyId !== null, access.readonly);
    const chatRuntime = goalId == null ? runtimeBadge(item) : execution ? runtimeBadge(execution) : unstarted.badge ? `<span class="runtime-tag">${unstarted.badge}</span>` : "";
    const chatModel = goalId == null ? `<button class="model-tag" data-act="model-menu" data-id="${esc(item.session_id)}" aria-haspopup="menu" title="切换模型和 effort"><span class="model-label">${esc(runtimeCatalog.optionsLabel(item))}</span>${ic("down", "xs")}</button>` : execution ? `<span class="model-tag" title="${esc(runtimeCatalog.optionsLabel({...execution, applied:null}))} · 本任务执行配置；总对话的模型设置不会改动已有任务"><span class="model-label">${esc(runtimeCatalog.optionsLabel({...execution, applied:null}))}</span></span>` : "";
    const identity = `${item.session_id}:${goalId ?? ""}:${access.readonly}:${execution?.id ?? ""}:${listing}:${access.readonlyReason ?? ""}`;
    if (mountedChat !== identity) {
      CrewChat.unmount();
      // A report being read in this goal survives a background identity change (read-only, execution, reopened round).
      const scaffold = '<div id="chat-head"></div><div id="goal-head"></div><div id="goal-chat-hint"></div><div id="chat-host" class="chat-host"></div>';
      if (goals) goals.replaceMain(scaffold); else $("main").innerHTML = scaffold;
      delete $("main").dataset.render;
      mountedChat = identity;
      if (!listing) CrewChat.mount({host:$("chat-host"), session:item, ...access, goalId, state, api, toast, canRead:() => !uiVisibility?.blocked(), beforeStop:() => people.confirmStop(item), onExecutionChange:() => goals?.refresh(goalId),
        async onRouted(id) { let target = (state.goals || []).find(goal => String(goal.id) === String(id)); if (!target) { await loadState(true); if (mountedChat !== identity) return; target = (state.goals || []).find(goal => String(goal.id) === String(id)); } if (target) { toast(`消息已送到任务「${target.title}」。`); navigate("chat", target.leader_session_id, item.session_id === target.leader_session_id ? null : item.session_id, target.id); } }});
    }
    $("main").classList.toggle("goal-list-mode", listing); $("chat-host").hidden = listing;
    $("goal-chat-hint").hidden = listing;
    const activeGoals = goalId == null ? (state.goals || []).filter(goal => (item.active_goal_ids || [item.active_goal_id]).some(id => String(id) === String(goal.id))) : [];
    domRenderer.replace($("goal-chat-hint"), activeGoals.length ? `<div class="goal-chat-hint">总对话可继续安排任务和验收。另有 ${activeGoals.length} 个任务：${activeGoals.map(goal => `<button type="button" class="text-link" data-act="active-goal" data-goal-id="${esc(goal.id)}" data-id="${esc(item.session_id)}">${esc(goal.title)} →</button>`).join(" ")}</div>` : unstarted?.hint ? `<div class="goal-chat-hint">${unstarted.hint}</div>` : "");
    const permissions = nativeApprovals?.countForChat(item.session_id, goalId, readOnlyId === null) || 0;
    const attentionCount = permissions + (readOnlyId === null ? pending.length + groupTodos.length : 0);
    syncWorkspace();
    domRenderer.replace($("native-approval-tray"), nativeApprovals?.chatMarkup(item.session_id, goalId, readOnlyId === null) || "");
    $("attention-scope").textContent = `${readOnlyId === null ? group?.name || item.name : item.name} · ${goalId == null ? "全部任务与总对话" : goals?.currentGoal()?.title || "当前任务"}`;
    domRenderer.replace($("chat-head"), `<div class="head">${readOnlyId === null ? groupAvatar(item, work) : av(item.name, 30)}<div class="heading-meta"><div class="title"><span class="chat-name" title="${esc(item.name)}">${esc(item.name)}</span>${readOnlyId === null ? priorityBadge(item.priority) : ""}${chatRuntime}</div><div class="sub"><span class="chat-scope" title="${esc(readOnlyId === null ? (item.goal ?? "") : `归 ${item.group} · 成员本人对话`)}">${esc(readOnlyId === null ? (item.goal ?? "") : `归 ${item.group} · 成员本人对话`)}</span>${group === undefined ? "" : `<span class="sep"></span><span class="chat-member-stats ${work.active ? "is-working" : ""}">${members.length} 个下属 · ${work.summary}</span>`}</div></div><div class="right">${chatModel}${goalId == null ? contextControls(item) : ""}${!listing ? `<button type="button" class="attention-trigger ${attentionCount ? "has-items" : ""}" data-act="attention" aria-controls="side-attention-section" aria-expanded="${conversationWorkspace?.expanded("attention") || false}" aria-label="待处理，${attentionCount} 件${readOnlyId === null && held.length ? `，另有 ${held.length} 件挂起` : ""}">${ic("inbox", "sm")}<span>待处理</span>${attentionCount ? `<span class="attention-count">${attentionCount}</span>` : ""}</button>` : ""}${readOnlyId === null ? "" : `<button class="btn ghost" data-act="leader" data-id="${esc(leaderId)}">回到 leader</button><button class="btn ghost" data-act="card" data-id="${esc(item.session_id)}">资料</button>`}${group === undefined || listing ? "" : `<button type="button" class="attention-trigger" data-act="task-panel" aria-label="${goalId == null ? "任务资料" : "本任务资料"}" aria-haspopup="dialog" aria-controls="side-task-section" title="${goalId == null ? "任务资料" : "本任务资料"}" aria-expanded="${conversationWorkspace?.expanded("tasks") || false}">${ic("file", "sm")}<span>${goalId == null ? "任务" : "资料"}</span></button><button class="icon-btn rp-toggle" data-act="right-open" title="本组架构图" aria-label="本组架构图">${ic("grid")}</button>`}<button class="icon-btn" data-act="session-menu" data-id="${esc(item.session_id)}" aria-haspopup="menu" title="会话操作" aria-label="会话操作">⋯</button></div></div>`);
    domRenderer.replace($("goal-head"), group ? goals?.groupMarkup(leaderId) || "" : "");
    domRenderer.replace($("decision-tray"), attention.markup);
    $("attention-empty").hidden = permissions + (readOnlyId === null ? pending.length + held.length + groupTodos.length : 0) > 0;
    domRenderer.replace($("side-task-body"), goals?.sideMarkup(leaderId) || "");
    $("side-task-title").textContent = goals?.currentGoal()?.title || "任务资料";
    $("side-attention-count").textContent = String(attentionCount);
    syncWorkspace();
    if (readOnlyId === null && leaderTrust !== null) {
      const slot = document.createElement("span"); slot.innerHTML = leaderTrust.indicator(item);
      const anchor = $("chat-head").querySelector('[data-act="session-menu"]');
      const existing = $("chat-head").querySelector('[data-act="leader-trust"]');
      if (existing) { if (existing.outerHTML !== slot.firstElementChild.outerHTML) existing.replaceWith(slot.firstElementChild); } else if (anchor) { const target = $("chat-head").querySelector('[data-act="attention"]') || anchor; target.before(slot.firstElementChild); }
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
  function renderTodos() { domRenderer.replace($("main"), attentionFeature.pageMarkup()); }
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
      body = state.leaders.slice().sort((a, b) => (a.priority === null ? 4 : a.priority) - (b.priority === null ? 4 : b.priority)).map(item => { const list = attentionFeature.pendingOf(item.session_id); list.forEach(item => grouped.add(item.id)); return list.length === 0 ? "" : `<section class="ov-group"><div class="ov-head">${av(item.name, 26)}<span class="ov-name">${esc(item.name)}</span><span class="ov-count">${list.length} 件</span><button class="btn ghost ov-go" data-act="leader" data-id="${esc(item.session_id)}">进入对话 ${ic("right", "sm")}</button></div><div class="ov-list">${list.map(attentionFeature.decisionCard).join("")}</div></section>`; }).join("");
      const ungrouped = pending.filter(item => !grouped.has(item.id));
      if (ungrouped.length > 0) body += `<section class="ov-group"><div class="ov-head"><span class="ov-name">未分组</span><span class="ov-count">${ungrouped.length} 件</span></div><div class="ov-list">${ungrouped.map(attentionFeature.decisionCard).join("")}</div></section>`;
      if (pending.length === 0 && held.length === 0) body = empty("没有等你拍板的事");
      body += attentionFeature.heldGroup(held, "all");
    } else body = working.length + dispatches.length === 0 ? empty("没有正在干活的会话") : `<div class="list">${working.map(workingRow).join("")}${dispatches.map(workingDispatchRow).join("")}</div>`;
    domRenderer.replace($("main"), `<div class="head big"><div><div class="title">全部</div><div class="sub">${esc(projectDisplay(state.project))} 的 ${state.leaders.length} 个 leader，${state.members.length} 个下属</div></div></div><div class="page"><div class="page-in ${allTab === "tree" ? "groups-page" : ""}"><div class="summary">${attentionFeature.openTodos().length > 0 ? `<button class="pill todo-summary" data-act="view" data-view="todos">${ic("check", "sm")}<b>${attentionFeature.openTodos().length}</b> 件待你动手 ${ic("right", "xs")}</button>` : ""}${nativeApprovals?.count() ? chip("native-approvals", "", " 执行权限待确认", nativeApprovals.count()) : ""}${chip("tree", ic("grid", "sm"), "架构图", null)}${chip("goals", ic("check", "sm"), "目标", (state.goals || []).length)}${chip("pending", '<i class="dot waiting"></i>', " 件等你拍板", pending.length)}${chip("working", '<i class="dot working"></i>', " 个在干活", working.length + dispatches.length)}<button class="pill tab" data-act="view" data-view="adopt">${ic("inbox", "sm")}会话库 · 查找与接入 ${ic("right", "xs")}</button></div>${body}</div></div>`);
  }
  function renderAdopt() {
    if (sessionLibrary === null) sessionLibrary = CrewSessionLibrary.create({api, toast,
      preferredLeader:() => leader(adoptLeaderId)?.name || "", changed:() => loadState(true),
      open:item => { const owner = item.role === "leader" ? item : state.leaders.find(person => person.name === item.group);
        if (owner) navigate("chat", owner.session_id, item.role === "leader" ? null : item.session_id); }});
    sessionLibrary.mount($("main"), state);
  }

  function focusDescriptor(node) { if (node === null || node === document.body) return null; return {node, act:node.dataset.act, id:node.dataset.id, view:node.dataset.view}; }
  function returnFocus(descriptor) {
    if (descriptor === null) return;
    if (descriptor.node.isConnected) { descriptor.node.focus({preventScroll:true}); return; }
    const target = Array.from(document.querySelectorAll("[data-act]")).find(item => item.dataset.act === descriptor.act && item.dataset.id === descriptor.id && item.dataset.view === descriptor.view);
    if (target) target.focus({preventScroll:true});
  }
  const sessionItem = id => state.leaders.concat(state.members).find(item => item.session_id === id);
  function hideSettings() { if (settingsOpen) closeSettings(); }
  function closeSettings() {
    settingsOpen = false; settingsUI?.hide(); $("app").inert = false;
    if (state !== null && project !== null) render();
    settingsReturn?.focus?.({preventScroll:true}); settingsReturn = null;
  }
  function openSettings(category = "ai") {
    closeTaskTools(); closeAuxiliary();
    usageUI?.unmount();
    settingsReturn = document.activeElement; settingsOpen = true;
    if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; }
    if (mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    CrewGit.unmount(); goals?.closeDrawer(); people?.closeCard(); menus.hide();
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
    closeTaskTools(); closeAuxiliary();
    usageUI?.unmount();
    if (window.navigator?.userAgent.includes("Electron/")) { location.assign(`/?workspace-settings=${encodeURIComponent(project)}`); return; }
    workspaceOpen = true;
    if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; }
    if (mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    CrewGit.unmount(); goals?.closeDrawer(); people?.closeCard(); menus.hide();
    void workspaceSettings.open(project);
  }
  function paletteCommands() {
    const items = state.leaders.map(item => ({group:"Leader", key:item.name + item.goal, html:`${av(item.name, 22)}${esc(item.name)}${priorityBadge(item.priority)}<span class="sm">${CrewChat.renderInline(item.goal, item.session_id)}</span>`, go:() => navigate("chat", item.session_id)}))
      .concat(state.members.map(item => ({group:"下属", key:item.task === null ? item.name : item.name + item.task, html:`${av(item.name, 22, true)}${esc(item.name)}<span class="sm">${esc(item.group)} · ${statusText[item.status]}</span>`, go:() => { goals?.closeList(); if (item.leader_session_id !== null) navigate("chat", item.leader_session_id, item.session_id); }})))
      .concat([{group:"操作", key:"起 leader 新建", html:`${ic("plus")}起 leader`, go:() => leaderCreate.open()}, {group:"操作", key:"全部 架构图", html:`${ic("grid")}全部`, go:() => navigate("all")}, {group:"操作", key:"会话库 待收编 接入", html:`${ic("inbox")}会话库`, go:() => navigate("adopt")}, {group:"操作", key:"用量 额度", html:`${ic("chart")}用量`, go:() => navigate("usage")}]);
    return items;
  }
  function syncRoute() {
    const hash = view === "chat" ? (readOnlyId === null ? `leader=${encodeURIComponent(leaderId)}` : `history=${encodeURIComponent(readOnlyId)}${leaderId === null ? "" : `&leader=${encodeURIComponent(leaderId)}`}`) + (goalId == null ? "" : `&goal=${encodeURIComponent(goalId)}`) : view;
    if (location.hash.slice(1) !== hash) history.replaceState(null, "", `${location.pathname}${location.search}#${hash}`);
  }
  function navigate(next, id = null, readonly = null, nextGoal = undefined) {
    if (nextGoal === undefined) nextGoal = next === "chat" && id === leaderId ? goalId : null;
    commentsFeature?.close(); leaderCreate?.close(); people?.close(); palette?.close();
    const sameScope = next === "chat" && view === "chat" && leaderId === id && String(goalId) === String(nextGoal);
    if (!sameScope || readOnlyId !== readonly) { closeTaskTools(); closeAuxiliary(); }
    if (view === "chat" && (!sameScope || readOnlyId !== readonly)) { CrewChat.unmount(); mountedChat = null; }
    if (!sameScope) goals?.closeDrawer();
    view = next; leaderId = id; readOnlyId = readonly; goalId = next === "chat" ? nextGoal : null;
    render(); syncRoute();
  }
  function readRoute() {
    const hash = location.hash.slice(1), params = new URLSearchParams(hash);
    goalId = params.get("goal");
    if (params.has("history")) { readOnlyId = params.get("history"); const item = member(readOnlyId); leaderId = item === undefined ? null : item.leader_session_id; view = "chat"; }
    else if (params.has("leader")) { leaderId = params.get("leader"); readOnlyId = null; view = "chat"; }
    else if (["all", "channel", "adopt", "usage", "git", "todos"].includes(hash)) { view = hash; leaderId = null; readOnlyId = null; }
    else if (hash === "" && attentionFeature.openTodos().length > 0) { view = "todos"; leaderId = null; readOnlyId = null; }
    else if (state.leaders.length > 0) { leaderId = state.leaders[0].session_id; readOnlyId = null; view = "chat"; }
    else view = "all";
    if (view === "chat" && (readOnlyId === null ? leader(leaderId) === undefined : member(readOnlyId) === undefined)) { view = "all"; leaderId = null; readOnlyId = null; }
    if (goalId !== null) { const goal = (state.goals || []).find(item => String(item.id) === goalId); if (!goal || goal.leader_session_id !== leaderId) goalId = null; }
  }
  function render() {
    syncWorkspace();
    if (settingsOpen || workspaceOpen) return;
    if (view !== "chat") goals?.sync(null, null);
    $("app").classList.toggle("wide", view !== "chat" || leaderId === null); renderSidebar();
    if (view !== "channel" && mountedChannel) { CrewChannel.unmount(); mountedChannel = false; }
    if (view !== "git") CrewGit.unmount();
    if (view !== "usage") usageUI?.unmount();
    if (view === "chat") renderChat(); else { if (mountedChat !== null) { CrewChat.unmount(); mountedChat = null; } if (view === "all") renderAll(); if (view === "todos") renderTodos(); if (view === "channel") renderChannel(); if (view === "adopt") renderAdopt(); if (view === "usage") usageUI.mount($("main")); if (view === "git") CrewGit.mount($("main"), {project, root:state.project.root, api, toast, catalog:runtimeCatalog.list, defaultRuntime:() => runtimeCatalog.taskRuntime("git"), onChanged:() => loadState(true), onSettings:() => gitSettings.open(project)}); }
    renderRight(); people?.update(); palette?.update(); document.title = `(${state.decisions.filter(item => !item.held).length}) ${projectDisplay(state.project)} · Crew`;
  }
  async function loadState(fresh = false) {
    if (pollBusy) { await statePromise; if (fresh) return loadState(true); return; }
    pollBusy = true;
    let complete;
    statePromise = new Promise(resolve => { complete = resolve; });
    const requestedGeneration = waitingNotifications.generation();
    try {
      const result = await api("/api/state"), previous = state; state = result; nativeApprovals?.sync(state); CrewActivity.sync(state); appearance.sync({themes:state.themes,project:project===null?null:state.project}); usageUI.update({project:state.project,limits:state.limits});
      if (project === null) { $("app").className = "workspace-entry"; $("sidebar").hidden = true; $("right-panel").hidden = true; domRenderer.replace($("main"), `<div class="workspace-home"><span class="workspace-logo" aria-hidden="true">${CrewTheme.logo()}</span><h1>Crew 工作区</h1><button class="btn ghost" data-act="settings">设置</button><p class="note">选一个项目，继续聊。</p><div class="list">${state.projects.map(item => `<a class="li project-entry" style="${CrewTheme.projectStyle(state.themes, item.theme)}" href="/?project=${encodeURIComponent(item.name)}"><span class="project-logo" aria-hidden="true">${CrewTheme.logo()}</span><span class="grow">${esc(projectDisplay(item))}</span><span class="project-theme-name">${esc(CrewTheme.get(state.themes, item.theme).name)}</span>${ic("right")}</a>`).join("")}</div></div>`); document.title = "工作区 · Crew"; return; }
      if (previous === null) readRoute();
      // A member can move or leave its group while its independent chat stays
      // open. Resolve the parent from this snapshot before composing controls.
      if (readOnlyId !== null && member(readOnlyId)) leaderId = member(readOnlyId).leader_session_id;
      if (readOnlyId !== null && !state.members.some(item => item.session_id === readOnlyId)) { navigate("all"); toast("该会话已不在下属列表，已返回全部"); }
      else if (view === "chat" && readOnlyId === null && leader(leaderId) === undefined) { navigate("all"); toast("这个 leader 已结束，已返回全部"); }
      else if (view === "chat" && goalId != null && !(state.goals || []).some(item => String(item.id) === String(goalId) && item.leader_session_id === leaderId)) { navigate("chat",leaderId,readOnlyId,null); toast("目标已不在本组，已回到总对话。"); }
      else { render(); syncRoute(); }
      waitingNotifications.sync(state,{fresh,requestedGeneration,reading:{view,leaderId,readOnlyId}});
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
  function setup() {
    domRenderer = window.CrewDOMRenderer.create({document});
    $("overlay-root").innerHTML = '<div class="menu" id="menu" role="menu"></div><div id="settings-parking" hidden><section class="settings-popover" id="settings" aria-label="外观设置"></section></div>';
    uiActions = window.CrewUIActions.create({onError:error => toast(error.message)});
    uiVisibility = window.CrewUIVisibility.create();
    uiVisibility.register("application",() => view !== "chat" || settingsOpen || workspaceOpen);
    menus = window.CrewUIMenu.create({element:$("menu"),document,window,visibility:uiVisibility,actions:uiActions,
      selection:{keepOpen:["set-model","set-effort","runtimes-refresh"],restoreFocus:["member-create","member-defaults","session-rename"]}});
    waitingNotifications = window.CrewWaitingNotifications.create({sound:CrewSound,visibility:uiVisibility,document});
    appearance = window.CrewAppearanceUI.create({host:$("settings"),document,window,renderer:domRenderer,theme:CrewTheme,sound:CrewSound,actions:uiActions,icon:ic,notify:toast,
      saveProjectTheme:(project,theme) => api("/api/project/theme",{project,theme}),
      onProjectTheme:async(name,theme) => { if(state?.project?.name===name)state.project.theme=theme;const item=state?.projects?.find(item=>item.name===name);if(item)item.theme=theme;renderSidebar();await loadState(true); },
      onModeChange:() => { if(state&&project!==null)renderSidebar(); }});
    usageUI = window.CrewUsage.create({api,queryPath,replace:domRenderer.replace,actions:uiActions,ui:{icon:ic,avatar:av,date,time},notify:toast});
    leaderOrdering = window.CrewLeaderOrder.create({document,sidebar:$("sidebar"),actions:uiActions,leaders:()=>state?.leaders||[],project:()=>state.project.name,
      savePriority:body=>api("/api/leaders/priority",body),saveOrder:body=>api("/api/leaders/order",body),refresh:()=>loadState(true),notify:toast,changed:renderSidebar});
    runtimeCatalog = window.CrewRuntimeCatalog.create({
      snapshot:() => ({catalog:state?.runtimes, legacyModels:state?.models, legacyEfforts:state?.efforts, choices:state?.ai}),
      discover:() => api("/api/runtimes")
    });
    runtimeModels = window.CrewRuntimeModels.create({actions:uiActions, catalog:runtimeCatalog, findSession:sessionItem,
      menu:menus,
      save:(id,changes) => api("/api/session/options",{session_id:id,...changes}), refresh:() => loadState(true), notify:toast, icon:ic, runtimeBadge
    });
    leaderCreate = window.CrewLeaderCreate.create({actions:uiActions,visibility:uiVisibility,catalog:runtimeCatalog,
      selection:() => state?.ai?.leader ?? state?.ai?.default,
      createLeader:payload => api("/api/leader",payload),refresh:() => loadState(true),navigate:id => navigate("chat",id),
      beforeOpen:() => { menus.hide(); hideSettings(); commentsFeature?.close(); people?.closeDialog(); },
      focus:{capture:() => focusDescriptor(menus.contains(document.activeElement) ? menus.anchor() : document.activeElement),restore:returnFocus}
    });
    conversationWorkspace = window.CrewConversationWorkspace.create({actions:uiActions,visibility:uiVisibility,
      onTaskList:() => goals?.showList(), closeMaterials:() => goals?.closeDrawer()
    });
    contextCompaction = window.CrewContextCompaction.create({actions:uiActions, findSession:sessionItem,
      subscribe:(id, callbacks) => CrewChat.watchCompact(id, {state, api, toast:callbacks.notify, onFinish:callbacks.onFinish}),
      request:(id, {afterTurn}) => api("/api/session/compact", {session_id:id, ...(afterTurn ? {after_turn:true} : {})}),
      refresh:() => loadState(true), changed:() => render(), notify:toast
    });
    attentionFeature = window.CrewAttention.create({actions:uiActions, state:() => state, api:(...args) => api(...args),
      refresh:() => loadState(true), notify:toast,
      changed:() => { if (view === "chat") renderChat(); else if (view === "todos") renderTodos(); else if (view === "all") renderAll(); },
      navigate:(id, targetGoal) => navigate("chat", id, null, targetGoal),
      rich:{inline:(...args) => CrewChat.renderInline(...args), markdown:(...args) => CrewChat.renderMarkdown(...args)},
      ui:{icon:ic, avatar:av, priority:priorityBadge, date, ago, empty}
    });
    commentsFeature = window.CrewComments.create({actions:uiActions,visibility:uiVisibility,
      people:() => ({leaders:state?.leaders || [], members:state?.members || []}), api:(...args) => api(...args),
      markdown:(...args) => CrewChat.renderMarkdown(...args), replace:domRenderer.replace, notify:toast,
      beforeOpen:() => { menus.hide(); hideSettings(); leaderCreate?.close(); people?.closeDialog(); },
      focus:{capture:() => focusDescriptor(menus.contains(document.activeElement) ? menus.anchor() : document.activeElement), restore:returnFocus}, ui:{icon:ic,date,ago}
    });
    window.addEventListener("beforeunload", () => {
      people.destroy(); leaderOrdering.destroy(); usageUI.destroy(); appearance.destroy(); palette.destroy(); waitingNotifications.destroy(); menus.destroy(); leaderCreate.destroy(); runtimeModels.destroy(); runtimeCatalog.destroy(); commentsFeature.destroy(); attentionFeature.destroy(); contextCompaction.destroy(); conversationWorkspace.destroy(); uiActions.destroy(); uiVisibility.destroy(); domRenderer.destroy();
    }, {once:true});
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
      onSnapshot(value) { if (state !== null) state.ai = value.ai; runtimeCatalog.replace(value.runtimes); },
      panels:{appearance(host) { host.append($("settings")); return () => $("settings-parking").append($("settings")); }, connections:settingsConnections, storage:settingsStorage}});
    CrewChat.initFiles(api, toast);
    if (window.CrewNativeApprovals) nativeApprovals = window.CrewNativeApprovals.create({api,toast,refreshState:() => loadState(true),onChange() { if (!state || project === null) return; renderSidebar(); if (view === "chat") renderChat(); else if (view === "all") renderAll(); }});
    if (window.CrewDocuments && project !== null) documents = window.CrewDocuments.create({project, api, toast,
      onChange:() => goals?.renderMaterials(),
      onSource(item) {
        const owner = leader(item.leader_session_id) || leader(item.session_id) || leader(member(item.session_id)?.leader_session_id);
        if (owner) navigate("chat", owner.session_id, member(item.session_id)?.session_id || null, item.goal_id ?? goalId); else toast("来源 leader 已不在当前列表中。");
      }});
    memberUI = window.CrewMembers.create({api,toast,getLeader:id=>leader(id),globalChoice:()=>state?.ai?.member ?? null,catalog:runtimeCatalog.list,
      refresh:async()=>{await runtimeCatalog.refresh();if(runtimeCatalog.status().error)throw Error(runtimeCatalog.status().error);},
      onChanged:async(id,parent)=>{await loadState(true);if(id){goals?.closeList();navigate("chat",parent,id,null);}}});
    if (window.CrewLeaderTrust) leaderTrust = window.CrewLeaderTrust.create({
      getLeader:id => state?.leaders.find(item => item.session_id === id), reportUrl:trustReportUrl, readReport:readTrustReport,
      renderMarkdown:(text,id) => CrewChat.renderMarkdown(text,id), save:body => api("/api/trust",body), onSaved:() => loadState(true), toast});
    if (window.CrewRuntimeSwitch) runtimeSwitch = window.CrewRuntimeSwitch.create({
      getMember:id => state?.members.concat(state?.leaders || []).find(item => item.session_id === id), catalog:runtimeCatalog.list, effortsFor:runtimeCatalog.efforts,
      refreshCatalog:runtimeCatalog.refresh, prepare:body => api("/api/session/runtime/prepare",body),
      switchRuntime:body => api("/api/session/runtime/switch",body), onChanged:() => { goals?.invalidate(); return loadState(true); }, toast});
    if (window.CrewGoals && project !== null) goals = window.CrewGoals.create({project, api, toast, state:() => state,
      refreshState:() => loadState(true), onChange:() => { if (state && view === "chat") renderChat(); }, onDrawer:closeTaskTools, onOverlay:closeTaskTools, onReturn:() => conversationWorkspace.open("tasks"), onReader:active => conversationWorkspace.setReader(active),
      renderDocuments:(items,id) => documents?.taskCards(items,id),
      onGeneral:id => navigate("chat", id, readOnlyId, null),
      onNavigate:id => { const item = (state.goals || []).find(item => String(item.id) === String(id)); if (item) { people?.closeCard(); navigate("chat", item.leader_session_id, null, item.id); } else toast("目标已不在当前工作区中，请刷新。"); }});
    people = window.CrewPeopleUI.create({document,actions:uiActions,visibility:uiVisibility,menu:menus,ordering:leaderOrdering,
      person:sessionItem,leaders:()=>state?.leaders||[],members:membersOf,inTask:()=>goalId!=null,runtimeId:runtimeCatalog.id,runtimeSwitch,modelMenu:runtimeModels.markup,
      relatedGoals:item=>(state?.goals||[]).filter(goal=>goal.leader_session_id===item.session_id&&(["running","pausing","waiting_user"].includes(goal.status)||String(goal.id)===String(item.active_goal_id))),
      api,refresh:()=>loadState(true),notify:toast,compact:contextCompaction.compact,replace:domRenderer.replace,changed:renderRight,
      beforeOpen:()=>{palette?.close();commentsFeature?.close();leaderCreate?.close();hideSettings();},
      interactionBlocked:()=>palette?.isOpen()||commentsFeature?.isOpen()||leaderCreate?.isOpen()||settingsOpen||workspaceOpen,
      focus:{capture:focusDescriptor,restore:returnFocus},ui:{icon:ic,avatar:av,status:st,context:contextControls,model:memberModel,activity:memberActivity,inline:CrewChat.renderInline,task:codexMarkup,date,time,runtime:runtimeBadge}});
    palette = window.CrewCommandPalette.create({document,actions:uiActions,visibility:uiVisibility,commands:paletteCommands,icon:ic,
      beforeOpen:()=>{people.closeDialog();menus.hide(menus.isOpen());hideSettings();commentsFeature?.close();leaderCreate?.close();},focus:{capture:()=>focusDescriptor(document.activeElement),restore:returnFocus}});
    document.addEventListener("click", event => {
      const button=event.target.closest("[data-act]");
      menus.handleClick(event);
      if(button===null||button.disabled||uiActions.handle(event))return;
      const act=button.dataset.act==="session-open"?button.dataset.openAct:button.dataset.act,id=button.dataset.id;
      if(act==="settings")openSettings();
      else if(act==="git-settings"){menus.hide();gitSettings.open(project);}
      else if(act==="workspace-settings")openWorkspaceSettings();
      else if(act==="member-add-menu")menus.show(button,`<button role="menuitem" data-act="member-create" data-id="${esc(id)}">${ic("plus","sm")}新建组员</button><button role="menuitem" data-act="member-adopt" data-id="${esc(id)}">${ic("inbox","sm")}收编已有会话</button>`);
      else if(act==="member-create"){menus.hide();memberUI.openCreate(id);}
      else if(act==="member-defaults"){menus.hide();memberUI.openDefaults(id);}
      else if(act==="member-adopt"){menus.hide();adoptLeaderId=id;navigate("adopt");}
      else if(act==="group-tree"){people.closeCard();navigate("chat",id);conversationWorkspace.open("tree");renderRight();syncWorkspace();}
      else if(act==="leader"){people.closeCard();goals?.closeList();navigate("chat",id);}
      else if(act==="active-goal"){const target=(state.goals||[]).find(goal=>String(goal.id)===button.dataset.goalId);if(target){goals?.closeList();navigate("chat",target.leader_session_id,id===target.leader_session_id?null:id,target.id);}}
      else if(act==="member-chat"||act==="member-history"){const item=member(id);if(item){people.closeCard();goals?.closeList();navigate("chat",item.leader_session_id,item.session_id);}}
      else if(act==="view"){people.closeCard();navigate(button.dataset.view);}
      else if(act==="all-tab"){allTab=button.dataset.tab;renderAll();}
      else if(act==="away")action("away",async()=>{await api("/api/notifications/away",{away:!state.notification.manual_away});await loadState(true);});
      else if(act==="leader-trust")leaderTrust?.open(id);
      else if(act==="leader-trust-report")leaderTrust?.open(id,true);
      else if(act==="runtime-switch")runtimeSwitch?.open(id);
      else if(act==="compact-department"){const item=leader(id);if(item)void maintenance.openDepartment(project,item);}
    });
    document.addEventListener("keydown", event => {
      if(event.defaultPrevented||commentsFeature?.isOpen()||leaderCreate?.isOpen()||palette?.isOpen()||workspaceOpen||event.key==="Escape"&&event.isComposing)return;
      if(menus.handleKey(event))return;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"&&state!==null&&project!==null){event.preventDefault();if(!people.confirmingStop())palette.open();return;}
      if(event.key==="Escape"&&settingsOpen){closeSettings();return;}
      if(settingsOpen)return;
      if(event.key==="Escape"){if(goals?.escape()){event.preventDefault();return;}if(!event.isComposing&&conversationWorkspace.escape())event.preventDefault();}
    });
    window.addEventListener("hashchange", () => { if (state === null || project === null) return; people.close();commentsFeature.close();leaderCreate.close();palette.close();CrewChat.unmount(); mountedChat = null; readRoute(); render(); });
    loadState(); setInterval(loadState, 3000);
  }
  setup();
})();
