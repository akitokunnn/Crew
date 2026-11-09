"use strict";
const CrewGit = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  let host = null, options = null, data = null, generation = 0, loadedAt = null, cachedScroll = 0;
  let loading = false, operation = null, errorText = "";
  let selectedRuntime = "", runtimeExplicit = false;
  let chatIdentity = null;
  const ago = at => {
    const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000);
    return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`;
  };
  const at = value => new Date(value).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  const branchIcon = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12m0-5h5a7 7 0 0 0 7-5"/></svg>';
  const requestLabels = {open:"已提合并请求", merging:"助手合并中", waiting_user:"合并等你拍板"};
  // Worktrees inside the repository read better relative to it; outside ones keep their full path.
  function shortPath(path) {
    const root = String(options.root).replace(/\\/g, "/").replace(/\/+$/, ""), value = String(path).replace(/\\/g, "/");
    return value.toLowerCase().startsWith(root.toLowerCase() + "/") ? value.slice(root.length + 1) : value;
  }
  // Prefer an owner still shown in the leader list, so its colour and avatar match the sidebar.
  const owner = branch => branch.owners.find(item => options.leader?.(item.session_id) !== undefined) ?? branch.owners[0] ?? null;
  function branchCard(branch) {
    const lead = owner(branch), request = data.requests.find(item => item.branch === branch.name && item.status in requestLabels);
    const color = lead === null ? "var(--text-3)" : options.tint?.(lead.name) ?? "var(--accent)";
    const count = `${branch.commits} 个提交${branch.state === "unmerged" ? "未合入" : "未推送"}`;
    const facts = [ago(branch.latest.time), branch.behind > 0 ? `落后 ${data.base.name} ${branch.behind}` : "", branch.worktree === null ? "" : shortPath(branch.worktree)].filter(Boolean);
    const who = lead === null ? '<span class="git-muted">没找到对应的 leader</span>'
      : `${options.avatar?.(lead.name, 18) ?? ""}<span class="git-lite-leader">${esc(lead.name)}</span>${branch.owners.length > 1 ? `<span class="git-muted">等 ${branch.owners.length} 位</span>` : ""}`;
    return `<article class="git-lite-card ${branch.state}" style="--tint:${color}"><div class="git-lite-card-head"><span class="git-lite-branch" title="${esc(branch.name)}">${branchIcon}<span>${esc(branch.name)}</span></span><span class="git-lite-count">${count}</span></div><div class="git-lite-owner">${who}${request === undefined ? "" : `<span class="git-lite-request ${request.status}">${requestLabels[request.status]} #${request.id}</span>`}</div><div class="git-lite-subject" title="${esc(branch.latest.subject)}">${esc(branch.latest.subject)}</div><div class="git-lite-facts" title="${esc([at(branch.latest.time), branch.latest.author, branch.worktree].filter(Boolean).join(" · "))}">${esc(facts.join(" · "))}</div></article>`;
  }
  function trunkSummary() {
    const base = data.base, stable = data.stable;
    const note = !base.origin ? `${base.name} 没有 origin，只看本地`
      : base.unpushed > 0 && base.behind_origin > 0 ? `本地 ${base.name} 与 origin 分叉：${base.unpushed} 个没推送，落后 ${base.behind_origin} 个`
      : base.unpushed > 0 ? `本地 ${base.name} 有 ${base.unpushed} 个提交还没推送`
      : base.behind_origin > 0 ? `本地 ${base.name} 落后 origin ${base.behind_origin} 个提交`
      : `本地 ${base.name} 与 origin 一致`;
    const lag = stable === null ? '<div class="git-lite-trunk-label">没有设置稳定分支</div>'
      : `<div class="git-lite-trunk-label">${esc(stable.name)} 落后 ${esc(base.name)}</div><div class="git-lite-trunk-number"><strong>${stable.behind}</strong> 个提交</div>`;
    return `<section class="git-lite-trunk" ${stable === null ? "" : `title="按 ${esc(stable.ref)} 与 ${esc(stable.base_ref)} 计算"`}>${lag}<div class="git-lite-trunk-note">${esc(note)}</div></section>`;
  }
  function branchOverview() {
    const section = (title, items, empty) => `<section class="git-lite-section"><h3>${esc(title)} <span>${items.length}</span></h3>${items.length === 0 ? `<div class="git-lite-empty">${empty}</div>` : `<div class="git-lite-cards">${items.map(branchCard).join("")}</div>`}</section>`;
    const unmerged = data.branches.filter(item => item.state === "unmerged"), unpushed = data.branches.filter(item => item.state === "unpushed");
    return `${trunkSummary()}${section(`未合入 ${data.base.name}`, unmerged, "所有分支都已合入")}${data.base.origin ? section(`已合入 ${data.base.name}，还没推送`, unpushed, "没有") : ""}${data.merged_hidden > 0 ? `<p class="git-lite-hidden">另有 ${data.merged_hidden} 个分支已合入并推送，不再显示</p>` : ""}`;
  }
  function enableCard() {
    const choices = data.choices, option = (name, selected) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`;
    const catalog = options.catalog?.() || [];
    if (!runtimeExplicit) selectedRuntime = options.defaultRuntime?.() || "";
    const available = catalog.some(item => item.id === selectedRuntime && item.available);
    const runtimeError = available ? "" : catalog.some(item => item.available) ? "所选执行工具当前不可用，请重新选择。" : "没有可用的执行工具，请安装并登录 Codex 或 Claude Code 后刷新。";
    return `<div class="page"><div class="git-enable-card"><span class="git-enable-icon">⑂</span><h1>启用 Git 助手</h1><p>Git 助手会在独立 worktree 中统一推送和合并。选择日常合入的主干和发布用的稳定分支后，即可在这里查看请求、分支与 GitHub 状态。</p><form id="git-enable-form"><label for="git-base">主干分支 <span>日常合入</span></label><select id="git-base" name="base">${choices.branches.map(name => option(name, choices.base)).join("")}</select><label for="git-stable">稳定分支 <span>可留空</span></label><select id="git-stable" name="stable"><option value="" ${choices.stable === null ? "selected" : ""}>不设置稳定分支</option>${choices.branches.map(name => option(name, choices.stable)).join("")}</select><label for="git-runtime">助手执行工具</label><select id="git-runtime" name="runtime"><option value="" disabled ${selectedRuntime === "" ? "selected" : ""}>请选择可用工具</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selectedRuntime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name)}${item.available ? "" : " · 未就绪"}</option>`).join("")}</select><p id="git-runtime-status" class="git-error" role="status" ${runtimeError ? "" : "hidden"}>${esc(runtimeError)}</p><button type="submit" class="btn primary" data-git-act="enable" ${operation !== null || choices.branches.length === 0 || !available ? "disabled" : ""}>${operation === "enable" ? "正在启用…" : "启用"}</button></form>${choices.branches.length === 0 ? '<p class="git-error">仓库没有本地分支，无法启用。</p>' : ""}${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</div></div>`;
  }
  const settingsButton = () => `<button type="button" class="btn" data-git-act="settings">Git 设置</button>`;
  const readStatus = () => loadedAt === null ? "" : `<span class="git-read-meta" role="status">读取于 ${esc(at(loadedAt))} · ${loading ? "正在更新，显示上次结果" : "按需更新"}</span>`;
  const refreshButton = () => `<button type="button" class="btn" data-git-act="refresh" title="重新读取视图，不拉取远端" ${loading || operation !== null ? "disabled" : ""}>${loading ? "正在更新…" : "更新视图"}</button>`;
  function render() {
    if (host === null) return;
    const scroll = host.querySelector(".git-page")?.scrollTop ?? cachedScroll;
    const assistant = options.state.git_assistant;
    // The assistant conversation comes from state and never waits for the repository scan.
    if (assistant === null || data?.available === false) {
      detachChat();
      if (data === null) { host.innerHTML = `<div id="git-view"><div class="head big git-head"><div class="title">Git</div>${settingsButton()}</div><div class="page"><div class="git-loading"><p class="note" role="${errorText ? "alert" : "status"}">${errorText === "" ? "正在读取仓库…" : esc(errorText)}</p>${errorText ? '<button type="button" class="btn" data-git-act="check">重新读取</button>' : ""}</div></div></div>`; return; }
      if (data.available === false) {
        host.innerHTML = `<div id="git-view"><div class="head big git-head"><div><div class="title">Git</div><div class="sub">${esc(data.repository)}${readStatus()}</div></div>${settingsButton()}</div><div class="page"><section class="git-enable-card"><span class="git-enable-icon" aria-hidden="true">⑂</span><h1>Git 功能暂不可用</h1><p role="status">${esc(data.reason)}</p><p>此工作区的聊天、任务和其他功能照常使用。Git 分支、提交与 worktree 功能需要 Git 仓库。</p><button type="button" class="btn" data-git-act="check" ${loading ? "disabled" : ""}>${loading ? "正在检查…" : "刷新检查"}</button>${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</section></div></div>`;
        return;
      }
      host.innerHTML = `<div id="git-view"><div class="head big git-head"><div><div class="title">${esc(data.repository)} <span class="git-heading-label">Git</span></div><div class="sub">${esc(data.repository)}${readStatus()}</div></div><div class="right">${refreshButton()}${settingsButton()}</div></div>${enableCard()}</div>`;
      return;
    }
    const fetchMeta = data === null ? "" : `<div class="git-fetch-meta">${data.last_fetch_at === null ? "尚未 fetch" : `上次 fetch · ${esc(at(data.last_fetch_at))}`}</div><button type="button" class="btn" data-git-act="fetch" title="执行 git fetch --prune origin" ${operation !== null || loading ? "disabled" : ""}>${operation === "fetch" ? "正在拉取…" : "拉取远端"}</button>`;
    const header = `<div><div class="title">${esc(assistant.name)} <span class="git-heading-label">Git</span></div><div class="sub">${data === null ? "" : esc(data.repository)}${readStatus()}</div></div><div class="right">${refreshButton()}${settingsButton()}${fetchMeta}</div>`;
    const access = options.chatAccess(assistant), identity = `${assistant.session_id}:${access.readonly}:${access.readonlyReason ?? ""}`;
    const summary = `<span class="git-assistant-branches"><b>${esc(assistant.base)}</b> → ${esc(assistant.stable === null ? "未设置稳定分支" : assistant.stable)}</span><span>进行中 ${assistant.requests.active}</span><span>等你 ${assistant.requests.waiting_user}</span><span>合并中 ${assistant.requests.merging}</span><span>排队 ${assistant.requests.queued}</span>`;
    const repository = data === null
      ? `<div class="git-loading"><p class="note" role="${errorText ? "alert" : "status"}">${errorText === "" ? "正在读取仓库…" : esc(errorText)}</p>${errorText ? '<button type="button" class="btn" data-git-act="check">重新读取</button>' : ""}</div>`
      : `<section id="git-panel" aria-label="分支">${branchOverview()}</section>`;
    if (chatIdentity !== identity) {
      detachChat();
      host.innerHTML = `<div id="git-view"><div class="head big git-head"></div><div class="git-assistant-summary" aria-label="Git 助手概况"></div><div class="git-operation-error"></div><div class="git-m-switch" role="radiogroup" aria-label="显示内容" hidden><input type="radio" name="git-m-pane" id="git-m-pane-repo" checked><label for="git-m-pane-repo">仓库</label><input type="radio" name="git-m-pane" id="git-m-pane-chat"><label for="git-m-pane-chat">对话</label></div><div class="git-body"><div id="git-chat-host" class="chat-host"></div><aside class="git-page" aria-label="仓库"><div class="git-page-in"></div></aside></div></div>`;
      CrewChat.mount({host:host.querySelector("#git-chat-host"), session:assistant, state:options.state, api:options.api, toast:options.toast, ...access,
        beforeStop:() => options.beforeStop(assistant), onRouted:options.onRouted});
      chatIdentity = identity;
    } else CrewChat.update(options.state);
    for (const [selector, markup] of [[".git-head",header],[".git-assistant-summary",summary],[".git-page-in",repository],[".git-operation-error",errorText === "" || data === null ? "" : `<pre class="git-error" role="alert">${esc(errorText)}</pre>`]]) {
      const element = host.querySelector(selector);
      if (element.innerHTML !== markup) element.innerHTML = markup;
    }
    if (scroll !== undefined && host.querySelector(".git-page") !== null) host.querySelector(".git-page").scrollTop = scroll;
  }
  async function load() {
    if (loading) return;
    loading = true;
    render();
    const requestGeneration = generation;
    try {
      const fresh = await options.api("/api/git");
      if (generation !== requestGeneration) return;
      data = fresh;
      loadedAt = new Date().toISOString(); errorText = "";
    } catch (error) { if (generation === requestGeneration) errorText = error.message; }
    finally { if (generation === requestGeneration) { loading = false; render(); } }
  }
  async function mutate(name, body) {
    if (operation !== null) return;
    const requestGeneration = ++generation;
    loading = false;
    operation = name; errorText = ""; render();
    try {
      const result = await options.api(`/api/git/${name}`, {project:options.project, ...body});
      if (generation !== requestGeneration) return;
      if (name === "fetch" && !result.ok) { errorText = result.error; data.last_fetch_at = result.last_fetch_at; }
      else { await load(); if (name === "enable" || name === "disable") await options.onChanged(); if (generation === requestGeneration) options.toast(name === "enable" ? "已启用 Git 助手" : name === "disable" ? "已停用 Git 助手" : result.output === "" ? "fetch 完成" : result.output); }
    } catch (error) { if (generation === requestGeneration) errorText = error.message; }
    finally { if (generation === requestGeneration) { operation = null; render(); } }
  }
  function click(event) {
    const button = event.target.closest("[data-git-act]");
    if (button === null) return;
    const act = button.dataset.gitAct;
    if (act === "settings") options.onSettings?.();
    else if (act === "check" || act === "refresh") load();
    else if (act === "fetch") mutate("fetch", {});
  }
  function submit(event) {
    if (event.target.id !== "git-enable-form" || data?.available === false) return;
    event.preventDefault();
    const runtime = event.target.elements.runtime.value;
    if (!(options.catalog?.() || []).some(item => item.id === runtime && item.available)) { errorText = "执行工具不可用，请检查安装和登录后重新选择。"; render(); return; }
    selectedRuntime = runtime; runtimeExplicit = true;
    mutate("enable", {base:event.target.elements.base.value, stable:event.target.elements.stable.value, runtime});
  }
  function change(event) {
    if (event.target.id !== "git-runtime") return;
    selectedRuntime = event.target.value; runtimeExplicit = true;
    const available = (options.catalog?.() || []).some(item => item.id === selectedRuntime && item.available);
    host.querySelector('[data-git-act="enable"]').disabled = !available || operation !== null || data.choices.branches.length === 0;
    const status = host.querySelector('#git-runtime-status');
    status.textContent = available ? "" : "所选执行工具当前不可用，请重新选择。"; status.hidden = available;
  }
  function mount(element, settings) {
    const sameWorkspace = options !== null && options.project === settings.project && options.root === settings.root;
    if (host === element && sameWorkspace) { options = settings; if (data !== null) render(); return; }
    unmount();
    if (!sameWorkspace) resetCache();
    host = element; options = settings;
    host.addEventListener("click", click); host.addEventListener("submit", submit); host.addEventListener("change", change);
    render(); if (data === null && !errorText) void load();
  }
  function detachChat() { if (chatIdentity !== null) { CrewChat.unmount(); chatIdentity = null; } }
  function unmount() {
    detachChat();
    cachedScroll = host?.querySelector(".git-page")?.scrollTop ?? cachedScroll;
    if (host !== null) { host.removeEventListener("click", click); host.removeEventListener("submit", submit); host.removeEventListener("change", change); }
    // A workspace owns this renderer. Leaving the Git page only detaches its UI;
    // keep the snapshot and any pending read so returning never restarts the scan.
    host = null;
  }
  function resetCache() {
    detachChat();
    generation++; data = null; loading = false; operation = null; errorText = ""; loadedAt = null; cachedScroll = 0;
    selectedRuntime = ""; runtimeExplicit = false;
  }
  function reload(project = options?.project) {
    if (options === null || project !== options.project) return;
    resetCache(); if (host !== null) { render(); void load(); }
  }
  return {mount, unmount, reload};
})();
