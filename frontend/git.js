"use strict";
const CrewGit = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  let host = null, options = null, data = null, generation = 0, loadedAt = null, cachedScroll = 0;
  let loading = false, operation = null, errorText = "";
  let selectedRuntime = "", runtimeExplicit = false;
  let chatIdentity = null;
  // The browser re-serializes markup (`<circle/>` → `<circle></circle>`), so innerHTML never equals what was
  // written; compare with the last written string, or every poll rebuilds the panel and closes an open picker.
  const written = new WeakMap();
  const ago = at => {
    const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000);
    return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`;
  };
  const at = value => new Date(value).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  const checkIcon = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  const branchIcon = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12m0-5h5a7 7 0 0 0 7-5"/></svg>';
  const requestLabels = {open:"已提合并请求", merging:"助手合并中", waiting_user:"合并等你拍板"};
  // Worktrees inside the repository read better relative to it; outside ones keep their full path.
  function shortPath(path) {
    const root = String(options.root).replace(/\\/g, "/").replace(/\/+$/, ""), value = String(path).replace(/\\/g, "/");
    return value.toLowerCase().startsWith(root.toLowerCase() + "/") ? value.slice(root.length + 1) : value;
  }
  // A branch has one leader at a time; handing its task over moves it.
  const owner = branch => branch.owners[0] ?? null;
  // Branches left by archived leaders: the user hands one to a current leader, recorded like a branch it created.
  function adoptPicker(branch) {
    const leaders = options.state.leaders || [];
    if (leaders.length === 0) return "";
    return `<select class="git-lite-adopt" data-git-adopt="${esc(branch.name)}" aria-label="把 ${esc(branch.name)} 交给 leader"${operation !== null ? ' disabled=""' : ""}><option value="" selected="" disabled="">${operation === "adopt" ? "正在交给…" : "交给 leader…"}</option>${leaders.map(item => `<option value="${esc(item.session_id)}">${esc(item.name)}</option>`).join("")}</select>`;
  }
  // Where a branch stands: not yet in base (or the local integration branch), waiting to be tested there, merged but
  // base not pushed, or merged with work still uncommitted in its worktree.
  function branchCount(branch) {
    const integration = data.integration?.name;
    if (branch.state === "integrated") return `${branch.commits} 个提交待实测`;
    if (branch.state === "unmerged") return integration ? `${branch.outside_integration} 个提交未进 ${integration}` : `${branch.commits} 个提交未合入`;
    return branch.state === "unpushed" ? `${branch.commits} 个提交未推送` : `已合入 ${data.base.name}`;
  }
  function branchTags(branch) {
    const tags = [branch.remote === null ? '<span class="git-lite-tag" title="origin 没有同名分支">只在本地</span>'
      : `<span class="git-lite-tag" title="origin/${esc(branch.name)}">${branch.remote.ahead > 0 ? `已推送 · 本地多 ${branch.remote.ahead} 个` : "已推送"}</span>`];
    if (branch.pr !== null) tags.push(`<a class="git-lite-tag pr" href="${esc(branch.pr.url)}" target="_blank" rel="noopener" title="打开 PR，目标 ${esc(branch.pr.base)}${data.pull_requests_at ? ` · 列表读取于 ${esc(at(data.pull_requests_at))}` : ""}">${branch.pr.draft ? "草稿 " : ""}PR #${branch.pr.number}</a>`);
    const dirty = branch.dirty;
    if (dirty?.error) tags.push(`<span class="git-lite-tag dirty" title="${esc(dirty.error)}">读取未提交改动失败</span>`);
    else if (dirty?.files) tags.push(`<span class="git-lite-tag dirty" title="worktree 里还没提交的文件${dirty.build_outputs ? `；另有 ${dirty.build_outputs} 个 Binaries / Intermediate 编译产物未计入` : ""}">${dirty.files} 个未提交改动</span>`);
    return `<div class="git-lite-tags">${tags.join("")}</div>`;
  }
  function branchCard(branch) {
    const lead = owner(branch), request = data.requests.find(item => item.branch === branch.name && item.status in requestLabels);
    const color = lead === null ? "var(--text-3)" : options.tint?.(lead.name) ?? "var(--accent)";
    const count = branchCount(branch);
    const facts = [ago(branch.latest.time), branch.state === "unmerged" && data.integration !== null && branch.commits > branch.outside_integration ? `共 ${branch.commits} 个未合入 ${data.base.name}` : "",
      branch.behind > 0 ? `落后 ${data.base.name} ${branch.behind}` : "", branch.worktree === null ? "" : shortPath(branch.worktree)].filter(Boolean);
    const who = lead === null ? `<span class="git-muted">没人认领</span>${adoptPicker(branch)}`
      : `${options.avatar?.(lead.name, 18) ?? ""}<span class="git-lite-leader">${esc(lead.name)}</span>`;
    return `<article class="git-lite-card ${branch.state}" style="--tint:${color}"><div class="git-lite-card-head"><span class="git-lite-branch" title="${esc(branch.name)}">${branchIcon}<span>${esc(branch.name)}</span></span><span class="git-lite-count">${count}</span></div><div class="git-lite-owner">${who}${request === undefined ? "" : `<span class="git-lite-request ${request.status}">${requestLabels[request.status]} #${request.id}</span>`}</div>${branchTags(branch)}<div class="git-lite-subject" title="${esc(branch.latest.subject)}">${esc(branch.latest.subject)}</div><div class="git-lite-facts" title="${esc([at(branch.latest.time), branch.latest.author, branch.worktree].filter(Boolean).join(" · "))}">${esc(facts.join(" · "))}</div></article>`;
  }
  const uncommitted = trunk => trunk.dirty?.error ? `${trunk.name} 工作区读取失败：${trunk.dirty.error}` : trunk.dirty?.files ? `${trunk.name} 工作区有 ${trunk.dirty.files} 个未提交改动` : "";
  function trunkSummary() {
    const base = data.base, integration = data.integration;
    const note = !base.origin ? `${base.name} 没有 origin，只看本地`
      : base.unpushed > 0 && base.behind_origin > 0 ? `本地 ${base.name} 与 origin 分叉：${base.unpushed} 个没推送，落后 ${base.behind_origin} 个`
      : base.unpushed > 0 ? `本地 ${base.name} 有 ${base.unpushed} 个提交还没推送`
      : base.behind_origin > 0 ? `本地 ${base.name} 落后 origin ${base.behind_origin} 个提交`
      : `本地 ${base.name} 与 origin 一致`;
    const baseBlock = `<div class="git-lite-trunk-label">主干 ${esc(base.name)}</div><div class="git-lite-trunk-note">${esc([note, uncommitted(base)].filter(Boolean).join(" · "))}</div>`;
    // The local integration branch is described by what it holds beyond base; its branches are listed below.
    const integrationBlock = integration === null ? ""
      : `<div class="git-lite-trunk-part" title="${esc(integration.branches.length ? `已进 ${integration.name}、未合入 ${base.name}：${integration.branches.join("、")}` : `没有分支停在 ${integration.name} 里待实测`)}"><div class="git-lite-trunk-label">本地集成分支 ${esc(integration.name)} <span>只在本机、不推送</span></div><div class="git-lite-trunk-number">比 ${esc(base.name)} 多 <strong>${integration.ahead}</strong> 个提交 · ${integration.branches.length} 个分支待实测</div><div class="git-lite-trunk-note">${esc([integration.behind > 0 ? `落后 ${base.name} ${integration.behind} 个提交` : `已包含 ${base.name} 的全部提交`, uncommitted(integration)].filter(Boolean).join(" · "))}</div></div>`;
    return `<section class="git-lite-trunk">${baseBlock}${integrationBlock}</section>`;
  }
  function branchOverview() {
    const pick = state => data.branches.filter(item => item.state === state), integration = data.integration?.name, base = data.base.name;
    // Each stage a branch can wait in; `short` names it in the one line that sums up the empty ones.
    const stages = (integration ? [{title: `还没进 ${integration}`, items: pick("unmerged"), short: `待进 ${integration}`},
      {title: `已进 ${integration}，待实测`, items: pick("integrated"), short: "待实测"}]
      : [{title: `未合入 ${base}`, items: pick("unmerged"), short: `待合入 ${base}`}])
      .concat(data.base.origin ? [{title: `已合入 ${base}，还没推送`, items: pick("unpushed"), short: "待推送"}] : []);
    const dirty = pick("merged");
    if (dirty.length) stages.push({title: `已合入 ${base}，worktree 还有未提交改动`, items: dirty});
    const busy = stages.filter(stage => stage.items.length), clear = stages.filter(stage => !stage.items.length);
    const section = stage => `<section class="git-lite-section"><h3>${esc(stage.title)} <span>${stage.items.length}</span></h3><div class="git-lite-cards">${stage.items.map(branchCard).join("")}</div></section>`;
    const none = `没有${clear.map(stage => stage.short).join("、")}的分支`;
    const hidden = data.merged_hidden > 0 ? `另有 ${data.merged_hidden} 个分支已合入并推送，不再显示` : "";
    if (!busy.length) return `${trunkSummary()}<section class="git-lite-clear">${checkIcon}<div><div class="git-lite-clear-title">分支都处理完了</div><div class="git-lite-clear-note">${esc(none)}</div>${hidden ? `<div class="git-lite-clear-note">${hidden}</div>` : ""}</div></section>`;
    return `${trunkSummary()}${busy.map(section).join("")}${clear.length ? `<p class="git-lite-clear-line">${checkIcon}${esc(none)}</p>` : ""}${hidden ? `<p class="git-lite-hidden">${hidden}</p>` : ""}`;
  }
  function enableCard() {
    const choices = data.choices, option = (name, selected) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`;
    const catalog = options.catalog?.() || [];
    if (!runtimeExplicit) selectedRuntime = options.defaultRuntime?.() || "";
    const available = catalog.some(item => item.id === selectedRuntime && item.available);
    // Nothing chosen yet is not an error: the enable button waits for a choice.
    const runtimeError = !catalog.some(item => item.available) ? "没有可用的执行工具，请安装并登录 Codex 或 Claude Code 后刷新。" : selectedRuntime && !available ? "所选执行工具当前不可用，请重新选择。" : "";
    return `<div class="page"><div class="git-enable-card"><span class="git-enable-icon">⑂</span><h1>启用 Git 助手</h1><p>Git 助手会在独立 worktree 中统一推送和合并。选择日常合入的主干后，即可在这里查看请求、分支与 GitHub 状态；如果你在本机另有一个只用来实测、不推送的集成分支，也可以一并选上。</p><form id="git-enable-form"><label for="git-base">主干分支 <span>日常合入</span></label><select id="git-base" name="base">${choices.branches.map(name => option(name, choices.base)).join("")}</select><label for="git-integration">本地集成分支 <span>可留空 · 只在本机、不推送</span></label><select id="git-integration" name="integration"><option value="" selected>不启用</option>${choices.branches.map(name => option(name, null)).join("")}</select><label for="git-runtime">助手执行工具</label><select id="git-runtime" name="runtime"><option value="" disabled ${selectedRuntime === "" ? "selected" : ""}>请选择可用工具</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selectedRuntime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name)}${item.available ? "" : " · 未就绪"}</option>`).join("")}</select><p id="git-runtime-status" class="git-error" role="status" ${runtimeError ? "" : "hidden"}>${esc(runtimeError)}</p><button type="submit" class="btn primary" data-git-act="enable" ${operation !== null || choices.branches.length === 0 || !available ? "disabled" : ""}>${operation === "enable" ? "正在启用…" : "启用"}</button></form>${choices.branches.length === 0 ? '<p class="git-error">仓库没有本地分支，无法启用。</p>' : ""}${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</div></div>`;
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
    const header = `<div><div class="title git-assistant-title"><span>${esc(assistant.name)} <span class="git-heading-label">Git</span></span>${options.contextControls(assistant, true)}</div><div class="sub">${data === null ? "" : esc(data.repository)}${readStatus()}</div></div><div class="right">${refreshButton()}${settingsButton()}${fetchMeta}</div>`;
    const access = options.chatAccess(assistant), identity = `${assistant.session_id}:${access.readonly}:${access.readonlyReason ?? ""}`;
    const summary = `<span class="git-assistant-branches">主干 <b>${esc(assistant.base)}</b>${assistant.integration === null ? "" : ` · 本地集成 <b>${esc(assistant.integration)}</b>`}</span><span>进行中 ${assistant.requests.active}</span><span>等你 ${assistant.requests.waiting_user}</span><span>合并中 ${assistant.requests.merging}</span><span>排队 ${assistant.requests.queued}</span>`;
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
    options.replace(host.querySelector(".git-head"), header);
    for (const [selector, markup] of [[".git-assistant-summary",summary],[".git-page-in",repository],[".git-operation-error",errorText === "" || data === null ? "" : `<pre class="git-error" role="alert">${esc(errorText)}</pre>`]]) {
      const element = host.querySelector(selector);
      if (written.get(element) !== markup) { element.innerHTML = markup; written.set(element, markup); }
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
      else { await load(); if (name !== "fetch") await options.onChanged(); if (generation === requestGeneration) options.toast(name === "enable" ? "已启用 Git 助手" : name === "disable" ? "已停用 Git 助手" : name === "adopt" ? `${result.branch} 已交给 ${result.leader.name}` : `${result.output === "" ? "fetch 完成" : result.output}${result.pull_requests_error ? `\nPR 列表未更新：${result.pull_requests_error}` : ""}`); }
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
    mutate("enable", {base:event.target.elements.base.value, integration:event.target.elements.integration.value, runtime});
  }
  function change(event) {
    if (event.target.id !== "git-runtime") {
      if (event.target.dataset.gitAdopt !== undefined) mutate("adopt", {branch:event.target.dataset.gitAdopt, leader_session_id:event.target.value});
      return;
    }
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
