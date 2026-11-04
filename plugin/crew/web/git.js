"use strict";
const CrewGit = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const DAY = 86400000;
  let host = null, options = null, data = null, generation = 0, loadedAt = null, cachedScroll = 0;
  let loading = false, operation = null, errorText = "";
  let selectedRuntime = "", runtimeExplicit = false;
  let chatIdentity = null, repositoryOpen = false;
  const expandedConflicts = new Set();
  const ago = at => {
    const seconds = Math.max(0, (Date.now() - new Date(at)) / 1000);
    return seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`;
  };
  const at = value => new Date(value).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  const inactive = branch => Date.now() - new Date(branch.latest.time) >= 30 * DAY;
  const ciText = {success:"✓ 通过", failure:"✗ 失败", pending:"进行中", none:"无 CI"};
  const ci = value => `<span class="git-ci ${value}"><i></i>${ciText[value]}</span>`;
  const prsFor = name => data.github === null ? [] : data.github.pull_requests.filter(pr => pr.head === name).sort((a, b) => Number(b.state === "OPEN") - Number(a.state === "OPEN") || b.number - a.number);
  const currentPR = branch => prsFor(branch.name).find(pr => pr.state === "OPEN");
  const sessionName = session => session.name === null ? "未命名" : session.name;
  const avatar = session => `<span class="git-avatar" title="${esc([sessionName(session), session.session_id, session.group, session.role, session.status].filter(value => value !== null).join(" · "))}">${esc(session.name === null ? "?" : session.name.slice(0, 1))}</span>`;
  const prTag = pr => `<a class="git-pr ${pr.ci}" href="${esc(pr.url)}" target="_blank" rel="noreferrer" title="${esc(pr.title)} · ${esc(pr.state)}${pr.draft ? " · 草稿" : ""}"><i></i>#${pr.number}${pr.draft ? " 草稿" : ""}</a>`;
  const branchNames = () => Array.from(new Set([...data.branches.map(item => item.name), ...data.remote_only.map(item => item.name.replace(/^origin\//, ""))])).sort();
  const branchColor = name => name === data.config.base || name === `origin/${data.config.base}` ? "var(--accent)" : name === data.config.stable || name === `origin/${data.config.stable}` ? "var(--text-3)" : `var(--git-color-${branchNames().filter(item => item !== data.config.base && item !== data.config.stable).indexOf(name.replace(/^origin\//, "")) % 7})`;
  function recentMerge() {
    const merge = data.recent_merge;
    if (merge === null) return '<div class="git-recent-merge git-muted">最近一次合入：暂无合并提交</div>';
    const pr = merge.pull_requests.find(item => item.merge_sha === merge.sha);
    const label = pr === undefined ? `${merge.parent_branches.slice(1).join(" + ")} → ${merge.parent_branches[0]}` : `PR #${pr.number} ${pr.title}`;
    const text = pr === undefined ? esc(label) : `<a href="${esc(pr.url)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
    return `<div class="git-recent-merge" title="${esc(new Date(merge.time).toLocaleString("zh-CN", {hour12:false}))}">最近一次合入：${text} · ${esc(new Date(merge.time).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false}))}</div>`;
  }
  function branchTag(name) {
    return `<span class="git-ref ${name.startsWith("origin/") ? "remote" : "local"}" style="--branch-color:${branchColor(name)}">${esc(name)}${name === data.config.base ? '<small>主干</small>' : name === data.config.stable ? '<small>稳定</small>' : ""}</span>`;
  }
  function statusTags(branch) {
    const statuses = [], pr = currentPR(branch), protectedBranch = branch.name === data.config.base || branch.name === data.config.stable;
    if (branch.conflicts.length > 0) statuses.push(["danger", "有冲突"]);
    if (branch.behind > 0 && !branch.merged && !protectedBranch) statuses.push(["warn", "落后要 rebase"]);
    if (pr !== undefined && pr.ci === "pending") statuses.push(["warn", "等 CI"]);
    if (pr !== undefined && pr.ci === "failure") statuses.push(["danger", "CI 红"]);
    if (branch.merged && !protectedBranch) statuses.push(["muted", "已合并可清理"]);
    if (inactive(branch)) statuses.push(["muted", "30 天没动"]);
    if (branch.worktrees.some(item => item.dirty)) statuses.push(["warn", "worktree 有没提交的改动"]);
    if (!protectedBranch && !branch.merged && branch.conflicts.length === 0 && branch.behind === 0 && (pr === undefined || !["pending", "failure"].includes(pr.ci))) statuses.push(["success", "可合并"]);
    return statuses.map(([kind, text]) => `<span class="git-status ${kind}">${text}</span>`).join("");
  }
  function branchTable() {
    const score = branch => branch.conflicts.length * 100 + Number(currentPR(branch)?.ci === "failure") * 80 + Number(branch.worktrees.some(item => item.dirty)) * 70 + Number(branch.behind > 0 && !branch.merged && branch.name !== data.config.base && branch.name !== data.config.stable) * 60 + Number(currentPR(branch)?.ci === "pending") * 50 + Number(branch.merged && branch.name !== data.config.base && branch.name !== data.config.stable) * 10 + Number(inactive(branch)) * 5;
    const rows = [...data.branches].sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name)).map(branch => `<tr data-git-row="${esc(branch.name)}"><td><div class="git-table-name">${branchTag(branch.name)}${branch.sessions.map(avatar).join("")}</div><div class="git-statuses">${statusTags(branch)}</div>${originRelation(branch)}${branch.sessions.length === 0 ? "" : `<div class="git-session-names">${branch.sessions.map(session => esc(sessionName(session))).join("、")}</div>`}</td><td>${branch.worktrees.length === 0 ? '<span class="git-muted">—</span>' : branch.worktrees.map(item => `<div class="git-worktree" title="${esc(item.path)}"><code>${esc(item.path)}</code>${item.dirty ? '<span class="git-dirty-dot" title="有没提交的改动"></span>' : ""}</div>`).join("")}</td><td><span class="git-counts"><b class="${branch.ahead > 0 ? "ahead" : ""}">↑ ${branch.ahead}</b><b class="${branch.behind > 0 ? "behind" : ""}">↓ ${branch.behind}</b></span></td><td>${branch.conflicts.length === 0 ? '<span class="git-clean">✓ 干净</span>' : `<details class="git-conflicts" data-git-conflicts="${esc(branch.name)}" ${expandedConflicts.has(branch.name) ? "open" : ""}><summary>冲突 ${branch.conflicts.length} 个文件</summary><ul>${branch.conflicts.map(file => `<li><code>${esc(file)}</code></li>`).join("")}</ul></details>`}</td><td><div class="git-table-pr">${prsFor(branch.name).map(pr => `<span>${prTag(pr)}${ci(pr.ci)}<span class="git-pr-state">${({OPEN:"开放", MERGED:"已合并", CLOSED:"已关闭"})[pr.state]}</span></span>`).join("")}${prsFor(branch.name).length === 0 ? '<span class="git-muted">—</span>' : ""}</div></td><td><div class="git-last-time" title="${esc(at(branch.latest.time))}">${ago(branch.latest.time)} · ${esc(branch.latest.author)}</div><div class="git-last-subject" title="${esc(branch.latest.subject)}">${esc(branch.latest.subject)}</div><code class="git-short-sha">${branch.latest.sha.slice(0, 7)}</code></td></tr>`).join("");
    return `<div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支与负责会话</th><th>Worktree</th><th>领先 / 落后主干</th><th>合并检查</th><th>PR 与 CI</th><th>最后提交</th></tr></thead><tbody>${rows}</tbody></table></div>${data.remote_only.length === 0 ? "" : `<section class="git-remote-only"><h3>远端独有分支 <span>${data.remote_only.length}</span></h3><div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支</th><th>最后提交</th><th>PR 与 CI</th></tr></thead><tbody>${data.remote_only.map(branch => `<tr><td>${branchTag(branch.name)}</td><td><div class="git-last-subject">${esc(branch.latest.subject)}</div><span class="git-last-time">${esc(branch.latest.author)} · ${ago(branch.latest.time)} · ${branch.latest.sha.slice(0, 7)}</span></td><td>${prsFor(branch.name.replace(/^origin\//, "")).map(prTag).join("")}</td></tr>`).join("")}</tbody></table></div></section>`}`;
  }
  function enableCard() {
    const choices = data.choices, option = (name, selected) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`;
    const catalog = options.catalog?.() || [];
    if (!runtimeExplicit) selectedRuntime = options.defaultRuntime?.() || "";
    const available = catalog.some(item => item.id === selectedRuntime && item.available);
    const runtimeError = available ? "" : catalog.some(item => item.available) ? "所选执行工具当前不可用，请重新选择。" : "没有可用的执行工具，请安装并登录 Codex 或 Claude Code 后刷新。";
    return `<div class="page"><div class="git-enable-card"><span class="git-enable-icon">⑂</span><h1>启用 Git 助手</h1><p>Git 助手会在独立 worktree 中统一推送和合并。选择日常合入的主干和发布用的稳定分支后，即可在这里查看请求、分支与 GitHub 状态。</p><form id="git-enable-form"><label for="git-base">主干分支 <span>日常合入</span></label><select id="git-base" name="base">${choices.branches.map(name => option(name, choices.base)).join("")}</select><label for="git-stable">稳定分支 <span>可留空</span></label><select id="git-stable" name="stable"><option value="" ${choices.stable === null ? "selected" : ""}>不设置稳定分支</option>${choices.branches.map(name => option(name, choices.stable)).join("")}</select><label for="git-runtime">助手执行工具</label><select id="git-runtime" name="runtime"><option value="" disabled ${selectedRuntime === "" ? "selected" : ""}>请选择可用工具</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selectedRuntime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name)}${item.available ? "" : " · 未就绪"}</option>`).join("")}</select><p id="git-runtime-status" class="git-error" role="status" ${runtimeError ? "" : "hidden"}>${esc(runtimeError)}</p><button type="submit" class="btn primary" data-git-act="enable" ${operation !== null || choices.branches.length === 0 || !available ? "disabled" : ""}>${operation === "enable" ? "正在启用…" : "启用"}</button></form>${choices.branches.length === 0 ? '<p class="git-error">仓库没有本地分支，无法启用。</p>' : ""}${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</div></div>`;
  }
  function mergeRequests() {
    const labels = {open:"待处理", merging:"合并中", waiting_user:"等用户拍板", merged:"已合并", rejected:"已拒绝"};
    const active = request => ["open", "merging", "waiting_user"].includes(request.status);
    const requests = [...data.requests].sort((a, b) => Number(active(b)) - Number(active(a)) || b.id - a.id);
    const rows = requests.map(request => {
      const pr = request.pr_number === null || data.github === null ? undefined : data.github.pull_requests.find(item => item.number === request.pr_number);
      return `<tr data-git-request="${request.id}"><td><span class="git-request-status ${request.status}">${labels[request.status]}</span></td><td><div class="git-request-description"><span>#${request.id}</span>${request.blocking ? '<b class="git-request-dependency">合并后继续</b>' : ""}${esc(request.description)}</div>${request.note === null ? "" : `<div class="git-request-note">${esc(request.note)}</div>`}</td><td><code>${esc(request.branch)}</code></td><td><span title="${esc(request.session_id)}">${esc(request.requester_name)}</span><div class="git-request-time">${esc(at(request.created_at))}</div></td><td>${request.pr_number === null ? '<span class="git-muted">—</span>' : pr === undefined ? `<span class="git-request-pr">#${request.pr_number}</span>` : prTag(pr)}</td></tr>`;
    }).join("");
    return `<section class="git-requests" aria-labelledby="git-requests-title"><div class="git-requests-head"><h2 id="git-requests-title">合并请求 <span>${requests.filter(active).length} 件待处理</span></h2><span>完成后用 <code>crew git request</code> 交给 Git 助手</span></div>${requests.length === 0 ? '<div class="git-requests-empty">还没有合并请求</div>' : `<div class="git-table-scroll"><table class="git-request-table"><thead><tr><th>状态</th><th>请求说明与结果</th><th>分支</th><th>提出者</th><th>PR</th></tr></thead><tbody>${rows}</tbody></table></div>`}</section>`;
  }
  function originRelation(branch) {
    return branch.origin_relation ? `<div class="git-overview-meta">${esc(branch.origin_relation)}</div>` : "";
  }

  function overview() {
    let baseCI = `<span class="git-muted">${data.github === null ? "GitHub 不可用" : "最新提交暂无 CI"}</span>`;
    if (data.github !== null) {
      const run = data.github.runs.find(item => item.sha === data.base.latest.sha);
      if (run !== undefined) {
        const state = run.status !== "completed" ? "pending" : run.conclusion === "success" ? "success" : ["failure", "cancelled", "timed_out", "action_required", "startup_failure"].includes(run.conclusion) ? "failure" : "none";
        baseCI = `<a class="git-run" href="${esc(run.url)}" target="_blank" rel="noreferrer">${ci(state)}<span>${run.sha.slice(0, 7)} · ${ago(run.time)}</span> ↗</a>`;
      }
    }
    return `<div class="git-overview"><section class="git-overview-card base"><div class="git-overview-label"><i></i>主干 <b>${esc(data.base.name)}</b>${baseCI}</div><div class="git-overview-subject">${esc(data.base.latest.subject)}</div><div class="git-overview-meta">${data.base.latest.sha.slice(0, 7)} · ${esc(data.base.latest.author)} · ${ago(data.base.latest.time)}</div>${originRelation(data.base)}${recentMerge()}</section>${data.stable === null ? '<section class="git-overview-card stable"><div class="git-overview-label"><i></i>稳定分支</div><div class="git-overview-subject git-muted">未设置</div><div class="git-overview-meta">启用时可选择发布用的稳定分支</div></section>' : `<section class="git-overview-card stable"><div class="git-overview-label"><i></i>稳定 <b>${esc(data.stable.name)}</b></div><div class="git-overview-subject">${esc(data.base.name)} 领先 <strong>${data.stable.ahead}</strong> 个提交</div><div class="git-overview-meta">${data.stable.latest.sha.slice(0, 7)} · ${esc(data.stable.latest.subject)}</div>${originRelation(data.stable)}</section>`}</div>`;
  }
  const settingsButton = () => `<button type="button" class="btn" data-git-act="settings">Git 设置</button>`;
  const readStatus = () => loadedAt === null ? "" : `<span class="git-read-meta" role="status">读取于 ${esc(at(loadedAt))} · ${loading ? "正在更新，显示上次结果" : "按需更新"}</span>`;
  const refreshButton = () => `<button type="button" class="btn" data-git-act="refresh" title="重新读取视图，不拉取远端" ${loading || operation !== null ? "disabled" : ""}>${loading ? "正在更新…" : "更新视图"}</button>`;
  function render() {
    if (host === null) return;
    const scroll = host.querySelector(".git-page")?.scrollTop ?? cachedScroll;
    if (data === null) { host.innerHTML = `<div id="git-view"><div class="head big git-head"><div class="title">Git</div>${settingsButton()}</div><div class="page"><div class="git-loading"><p class="note" role="${errorText ? "alert" : "status"}">${errorText === "" ? "正在读取仓库…" : esc(errorText)}</p>${errorText ? '<button type="button" class="btn" data-git-act="check">重新读取</button>' : ""}</div></div></div>`; return; }
    if (data.available === false) {
      detachChat();
      host.innerHTML = `<div id="git-view"><div class="head big git-head"><div><div class="title">Git</div><div class="sub">${esc(data.repository)}${readStatus()}</div></div>${settingsButton()}</div><div class="page"><section class="git-enable-card"><span class="git-enable-icon" aria-hidden="true">⑂</span><h1>Git 功能暂不可用</h1><p role="status">${esc(data.reason)}</p><p>此工作区的聊天、任务和其他功能照常使用。Git 分支、提交与 worktree 功能需要 Git 仓库。</p><button type="button" class="btn" data-git-act="check" ${loading ? "disabled" : ""}>${loading ? "正在检查…" : "刷新检查"}</button>${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</section></div></div>`;
      return;
    }
    const assistant = data.enabled ? options.state.git_assistant : null;
    const header = `<div><div class="title">${esc(assistant === null ? data.repository : assistant.name)} <span class="git-heading-label">Git</span></div><div class="sub">${esc(data.repository)}${readStatus()}</div></div><div class="right">${refreshButton()}${settingsButton()}${assistant === null ? "" : `<div class="git-fetch-meta">${data.last_fetch_at === null ? "尚未 fetch" : `上次 fetch · ${esc(at(data.last_fetch_at))}`}</div><button type="button" class="btn" data-git-act="fetch" title="执行 git fetch --prune origin" ${operation !== null || loading ? "disabled" : ""}>${operation === "fetch" ? "正在拉取…" : "拉取远端"}</button>`}</div>`;
    if (assistant === null) {
      detachChat();
      host.innerHTML = `<div id="git-view"><div class="head big git-head">${header}</div>${enableCard()}</div>`;
      return;
    }
    const access = options.chatAccess(assistant), identity = `${assistant.session_id}:${access.readonly}:${access.readonlyReason ?? ""}`;
    const summary = `<span class="git-assistant-branches"><b>${esc(assistant.base)}</b> → ${esc(assistant.stable === null ? "未设置稳定分支" : assistant.stable)}</span><span>进行中 ${assistant.requests.active}</span><span>等你 ${assistant.requests.waiting_user}</span><span>合并中 ${assistant.requests.merging}</span><span>排队 ${assistant.requests.queued}</span>`;
    const repository = `${overview()}${mergeRequests()}${data.github === null ? `<div class="git-github-unavailable"><strong>GitHub 不可用</strong><pre>${esc(data.github_reason)}</pre></div>` : ""}<section id="git-panel" aria-label="分支">${branchTable()}</section>`;
    if (chatIdentity !== identity) {
      detachChat();
      host.innerHTML = `<div id="git-view"><div class="head big git-head"></div><div class="git-assistant-summary" aria-label="Git 助手概况"></div><div class="git-operation-error"></div><div class="git-page"><details class="git-repository-details" ${repositoryOpen ? "open" : ""}><summary>分支与合并请求</summary><div class="git-page-in"></div></details></div><div id="git-chat-host" class="chat-host"></div></div>`;
      host.querySelector(".git-repository-details").addEventListener("toggle", event => { repositoryOpen = event.target.open; });
      CrewChat.mount({host:host.querySelector("#git-chat-host"), session:assistant, state:options.state, api:options.api, toast:options.toast, ...access,
        beforeStop:() => options.beforeStop(assistant), onRouted:options.onRouted});
      chatIdentity = identity;
    } else CrewChat.update(options.state);
    for (const [selector, markup] of [[".git-head",header],[".git-assistant-summary",summary],[".git-page-in",repository],[".git-operation-error",errorText === "" ? "" : `<pre class="git-error" role="alert">${esc(errorText)}</pre>`]]) {
      const element = host.querySelector(selector);
      if (element.innerHTML !== markup) element.innerHTML = markup;
    }
    if (scroll !== undefined && host.querySelector(".git-page") !== null) host.querySelector(".git-page").scrollTop = scroll;
    for (const detail of host.querySelectorAll(".git-conflicts")) detail.ontoggle = () => { if (detail.open) expandedConflicts.add(detail.dataset.gitConflicts); else expandedConflicts.delete(detail.dataset.gitConflicts); };
  }
  async function load() {
    if (loading) return;
    loading = true;
    render();
    const requestGeneration = generation;
    try {
      const fresh = await options.api("/api/git?offset=0");
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
    detachChat(); repositoryOpen = false;
    generation++; data = null; loading = false; operation = null; errorText = ""; loadedAt = null; cachedScroll = 0;
    selectedRuntime = ""; runtimeExplicit = false; expandedConflicts.clear();
  }
  function reload(project = options?.project) {
    if (options === null || project !== options.project) return;
    resetCache(); if (host !== null) { render(); void load(); }
  }
  return {mount, unmount, reload};
})();
