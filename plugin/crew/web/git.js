"use strict";
const CrewGit = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const DAY = 86400000, ROW = 66, GAP = 19;
  let host = null, options = null, data = null, timer = null, generation = 0;
  let tab = "graph", activeOnly = false, selectedBranch = null, loading = false, operation = null, errorText = "", pages = 1;
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
  function branchTag(name, filterActive = false) {
    const remote = name.startsWith("origin/"), branch = data.branches.find(item => item.name === name);
    const local = data.branches.find(item => item.name === name.replace(/^origin\//, ""));
    if (filterActive && activeOnly && local !== undefined && local.merged && inactive(local)) return "";
    const color = branchColor(name), label = `${esc(name)}${name === data.config.base ? '<small>主干</small>' : name === data.config.stable ? '<small>稳定</small>' : ""}`;
    return `<button type="button" class="git-ref ${remote ? "remote" : "local"} ${selectedBranch === name ? "selected" : ""}" style="--branch-color:${color}" data-git-branch="${esc(name)}" aria-pressed="${selectedBranch === name}" title="高亮相对 ${esc(data.config.base)} 独有的 ${data.ref_unique_commits[name].length} 个提交">${label}</button>`;
  }
  function graphLayout() {
    const commits = data.commits, bySha = new Map(commits.map(item => [item.sha, item])), lanes = new Map(), branchLanes = new Map([[data.config.base, 0]]);
    if (data.config.stable !== null) branchLanes.set(data.config.stable, 1);
    let laneCount = 2;
    const branches = [...data.branches.map(item => item.name), ...data.remote_only.map(item => item.name)].sort();
    for (const name of branchNames()) if (!branchLanes.has(name)) branchLanes.set(name, laneCount++);
    for (const name of branches) branchLanes.set(name, branchLanes.get(name.replace(/^origin\//, "")));
    const baseHead = commits.find(item => item.refs.includes(data.config.base));
    const stableHead = commits.find(item => item.refs.includes(data.config.stable));
    if (baseHead !== undefined) lanes.set(baseHead.sha, 0);
    if (stableHead !== undefined && stableHead.sha !== baseHead?.sha) lanes.set(stableHead.sha, 1);
    const priority = [...new Set([data.config.stable, data.config.base, ...branches].filter(name => name !== null))];
    for (const name of priority) {
      const head = commits.find(item => item.refs.includes(name));
      if (head === undefined) continue;
      let commit = head;
      const lane = branchLanes.get(name);
      while (commit !== undefined) {
        if (lanes.has(commit.sha) && lanes.get(commit.sha) !== lane) break;
        lanes.set(commit.sha, lane);
        commit = bySha.get(commit.parents[0]);
      }
    }
    // Merge parents without surviving refs still need a continuous lane of their own.
    for (const commit of commits) {
      if (!lanes.has(commit.sha)) lanes.set(commit.sha, laneCount++);
      commit.parents.forEach((parent, index) => {
        if (bySha.has(parent) && !lanes.has(parent)) lanes.set(parent, index === 0 ? lanes.get(commit.sha) : laneCount++);
      });
    }
    const index = new Map(commits.map((item, row) => [item.sha, row])), width = laneCount * GAP + 20, height = commits.length * ROW;
    const x = sha => 14 + lanes.get(sha) * GAP, y = row => row * ROW + ROW / 2;
    let lines = "", nodes = "";
    for (const commit of commits) {
      const row = index.get(commit.sha), fromX = x(commit.sha), fromY = y(row);
      const color = lanes.get(commit.sha) === 0 ? "var(--accent)" : lanes.get(commit.sha) === 1 ? "var(--text-3)" : `var(--git-color-${(lanes.get(commit.sha) - 2) % 7})`;
      for (const parent of commit.parents) {
        const parentRow = index.get(parent), toX = parentRow === undefined ? fromX : x(parent), toY = parentRow === undefined ? height : y(parentRow);
        const turn = Math.min(toY - 10, fromY + ROW / 2);
        lines += `<path d="M ${fromX} ${fromY} L ${fromX} ${turn - 8} Q ${fromX} ${turn} ${(fromX + toX) / 2} ${turn + 4} Q ${toX} ${turn + 8} ${toX} ${turn + 16} L ${toX} ${toY}" stroke="${color}" data-git-from="${commit.sha}" data-git-to="${parent}"/>`;
      }
      nodes += `<circle cx="${fromX}" cy="${fromY}" r="4.5" fill="${color}" data-git-node="${commit.sha}"/>`;
    }
    return {width, height, svg:`<svg class="git-lanes" viewBox="0 0 ${width} ${height}" style="width:calc(${width}px * var(--fs));height:calc(${height}px * var(--fs))" aria-label="提交父子关系图"><g class="git-edges">${lines}</g><g class="git-nodes">${nodes}</g></svg>`};
  }
  function graph() {
    const layout = graphLayout(), unique = new Set(selectedBranch === null ? [] : data.ref_unique_commits[selectedBranch]);
    const rows = data.commits.map(commit => {
      const locals = data.branches.filter(branch => commit.refs.includes(branch.name)), prs = new Map(), sessions = new Map();
      for (const ref of commit.refs) for (const pr of prsFor(ref.replace(/^origin\//, ""))) prs.set(pr.number, pr);
      for (const branch of locals) for (const session of branch.sessions) sessions.set(session.session_id, session);
      return `<div class="git-commit ${selectedBranch === null ? "" : unique.has(commit.sha) ? "is-highlighted" : "is-muted"}" data-git-sha="${commit.sha}"><div class="git-commit-top"><span class="git-subject" title="${esc(commit.subject)}">${esc(commit.subject)}</span><span class="git-sha">${commit.sha.slice(0, 7)}</span></div><div class="git-commit-bottom"><span class="git-refs">${commit.refs.map(name => branchTag(name, true)).join("")}${Array.from(prs.values()).map(prTag).join("")}${Array.from(sessions.values()).map(avatar).join("")}</span><span class="git-commit-meta">${esc(commit.author)}<span title="${esc(at(commit.time))}">${ago(commit.time)}</span></span></div></div>`;
    }).join("");
    return `<div class="git-graph-toolbar"><span class="git-graph-key"><i style="background:var(--accent)"></i>${esc(data.config.base)}${data.config.stable === null ? "" : `<i style="background:var(--text-3)"></i>${esc(data.config.stable)}`}</span><span class="gap"></span>${selectedBranch === null ? "" : `<button type="button" class="git-selection" data-git-act="clear-selection">${esc(selectedBranch)} · ${unique.size} 个独有提交 ×</button>`}<button type="button" class="switch ${activeOnly ? "on" : ""}" role="switch" aria-checked="${activeOnly}" data-git-act="active"><span class="track"></span>只看活跃分支</button></div><div class="git-graph-scroll"><div class="git-graph" style="grid-template-columns:calc(${layout.width}px * var(--fs)) minmax(460px,1fr)">${layout.svg}<div class="git-commits">${rows}</div></div></div>${data.commits.length === 0 ? '<p class="note">仓库还没有提交。</p>' : ""}<div class="git-load-more">${data.next_offset === null ? '<span class="note">已加载全部提交</span>' : `<button type="button" class="btn" data-git-act="load-more" ${loading ? "disabled" : ""}>${loading ? "正在加载…" : "加载更早的"}</button>`}<span class="note">${data.commits.length} 个提交</span></div>`;
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
    const rows = [...data.branches].sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name)).map(branch => `<tr data-git-row="${esc(branch.name)}"><td><div class="git-table-name">${branchTag(branch.name)}${branch.sessions.map(avatar).join("")}</div><div class="git-statuses">${statusTags(branch)}</div>${branch.sessions.length === 0 ? "" : `<div class="git-session-names">${branch.sessions.map(session => esc(sessionName(session))).join("、")}</div>`}</td><td>${branch.worktrees.length === 0 ? '<span class="git-muted">—</span>' : branch.worktrees.map(item => `<div class="git-worktree" title="${esc(item.path)}"><code>${esc(item.path)}</code>${item.dirty ? '<span class="git-dirty-dot" title="有没提交的改动"></span>' : ""}</div>`).join("")}</td><td><span class="git-counts"><b class="${branch.ahead > 0 ? "ahead" : ""}">↑ ${branch.ahead}</b><b class="${branch.behind > 0 ? "behind" : ""}">↓ ${branch.behind}</b></span></td><td>${branch.conflicts.length === 0 ? '<span class="git-clean">✓ 干净</span>' : `<details class="git-conflicts" data-git-conflicts="${esc(branch.name)}" ${expandedConflicts.has(branch.name) ? "open" : ""}><summary>冲突 ${branch.conflicts.length} 个文件</summary><ul>${branch.conflicts.map(file => `<li><code>${esc(file)}</code></li>`).join("")}</ul></details>`}</td><td><div class="git-table-pr">${prsFor(branch.name).map(pr => `<span>${prTag(pr)}${ci(pr.ci)}<span class="git-pr-state">${({OPEN:"开放", MERGED:"已合并", CLOSED:"已关闭"})[pr.state]}</span></span>`).join("")}${prsFor(branch.name).length === 0 ? '<span class="git-muted">—</span>' : ""}</div></td><td><div class="git-last-time" title="${esc(at(branch.latest.time))}">${ago(branch.latest.time)} · ${esc(branch.latest.author)}</div><div class="git-last-subject" title="${esc(branch.latest.subject)}">${esc(branch.latest.subject)}</div><code class="git-short-sha">${branch.latest.sha.slice(0, 7)}</code></td></tr>`).join("");
    return `<div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支与负责会话</th><th>Worktree</th><th>领先 / 落后主干</th><th>合并检查</th><th>PR 与 CI</th><th>最后提交</th></tr></thead><tbody>${rows}</tbody></table></div>${data.remote_only.length === 0 ? "" : `<section class="git-remote-only"><h3>远端独有分支 <span>${data.remote_only.length}</span></h3><div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支</th><th>最后提交</th><th>PR 与 CI</th></tr></thead><tbody>${data.remote_only.map(branch => `<tr><td>${branchTag(branch.name)}</td><td><div class="git-last-subject">${esc(branch.latest.subject)}</div><span class="git-last-time">${esc(branch.latest.author)} · ${ago(branch.latest.time)} · ${branch.latest.sha.slice(0, 7)}</span></td><td>${prsFor(branch.name.replace(/^origin\//, "")).map(prTag).join("")}</td></tr>`).join("")}</tbody></table></div></section>`}`;
  }
  function enableCard() {
    const choices = data.choices, option = (name, selected) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`;
    return `<div class="page"><div class="git-enable-card"><span class="git-enable-icon">⑂</span><h1>启用 Git 管理</h1><p>在 Crew 里查看仓库的分支、提交关系、合并冲突与 GitHub 状态。启用后选择日常合入的主干和发布用的稳定分支。</p><form id="git-enable-form"><label for="git-base">主干分支 <span>日常合入</span></label><select id="git-base" name="base">${choices.branches.map(name => option(name, choices.base)).join("")}</select><label for="git-stable">稳定分支 <span>可留空</span></label><select id="git-stable" name="stable"><option value="" ${choices.stable === null ? "selected" : ""}>不设置稳定分支</option>${choices.branches.map(name => option(name, choices.stable)).join("")}</select><button type="submit" class="btn primary" data-git-act="enable" ${operation !== null || choices.branches.length === 0 ? "disabled" : ""}>${operation === "enable" ? "正在启用…" : "启用"}</button></form>${choices.branches.length === 0 ? '<p class="git-error">仓库没有本地分支，无法启用。</p>' : ""}${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</div></div>`;
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
    return `<div class="git-overview"><section class="git-overview-card base"><div class="git-overview-label"><i></i>主干 <b>${esc(data.base.name)}</b>${baseCI}</div><div class="git-overview-subject">${esc(data.base.latest.subject)}</div><div class="git-overview-meta">${data.base.latest.sha.slice(0, 7)} · ${esc(data.base.latest.author)} · ${ago(data.base.latest.time)}</div></section>${data.stable === null ? '<section class="git-overview-card stable"><div class="git-overview-label"><i></i>稳定分支</div><div class="git-overview-subject git-muted">未设置</div><div class="git-overview-meta">启用时可选择发布用的稳定分支</div></section>' : `<section class="git-overview-card stable"><div class="git-overview-label"><i></i>稳定 <b>${esc(data.stable.name)}</b></div><div class="git-overview-subject">${esc(data.base.name)} 领先 <strong>${data.stable.ahead}</strong> 个提交</div><div class="git-overview-meta">${data.stable.latest.sha.slice(0, 7)} · ${esc(data.stable.latest.subject)}</div></section>`}</div>`;
  }
  function render() {
    if (host === null) return;
    const scroll = host.querySelector(".git-page")?.scrollTop;
    if (data === null) { host.innerHTML = `<div id="git-view"><div class="head big"><div class="title">Git</div></div><div class="page"><p class="note git-loading">${errorText === "" ? "正在读取仓库…" : esc(errorText)}</p></div></div>`; return; }
    const header = `<div class="head big git-head"><div><div class="title">${esc(data.repository)} <span class="git-heading-label">Git</span></div><div class="sub">${data.enabled ? "分支、提交与合并检查" : "项目 Git 管理"}</div></div>${data.enabled ? `<div class="right"><div class="git-fetch-meta">${data.last_fetch_at === null ? "尚未 fetch" : `上次 fetch · ${esc(at(data.last_fetch_at))}`}</div><button type="button" class="btn" data-git-act="fetch" ${operation !== null ? "disabled" : ""}>${operation === "fetch" ? "正在 fetch…" : "刷新（fetch）"}</button><details class="git-more"><summary aria-label="更多" title="更多">•••</summary><div><button type="button" data-git-act="disable">停用 Git 管理</button></div></details></div>` : ""}</div>`;
    host.innerHTML = `<div id="git-view">${header}${data.enabled ? `<div class="page git-page"><div class="git-page-in">${overview()}${errorText === "" ? "" : `<pre class="git-error" role="alert">${esc(errorText)}</pre>`}${data.github === null ? `<div class="git-github-unavailable"><strong>GitHub 不可用</strong><pre>${esc(data.github_reason)}</pre></div>` : ""}<div class="git-tabs" role="tablist" aria-label="Git 视图"><button type="button" role="tab" id="git-graph-tab" aria-controls="git-panel" aria-selected="${tab === "graph"}" data-git-tab="graph" class="${tab === "graph" ? "on" : ""}">树状图 <span>${data.commits.length}</span></button><button type="button" role="tab" id="git-branches-tab" aria-controls="git-panel" aria-selected="${tab === "branches"}" data-git-tab="branches" class="${tab === "branches" ? "on" : ""}">分支 <span>${data.branches.length}</span></button></div><section id="git-panel" role="tabpanel" aria-labelledby="git-${tab}-tab">${tab === "graph" ? graph() : branchTable()}</section></div></div>` : enableCard()}<dialog id="git-disable-dialog" class="git-disable-dialog" aria-labelledby="git-disable-title"><h2 id="git-disable-title">停用 Git 管理？</h2><p>此项目的 Git 管理配置将被删除。</p><div><button type="button" class="btn" data-git-act="cancel-disable">取消</button><button type="button" class="btn danger" data-git-act="confirm-disable" ${operation !== null ? "disabled" : ""}>确认停用</button></div></dialog></div>`;
    if (scroll !== undefined && host.querySelector(".git-page") !== null) host.querySelector(".git-page").scrollTop = scroll;
    for (const detail of host.querySelectorAll(".git-conflicts")) detail.addEventListener("toggle", () => { if (detail.open) expandedConflicts.add(detail.dataset.gitConflicts); else expandedConflicts.delete(detail.dataset.gitConflicts); });
  }
  async function load(append = false) {
    if (loading) return;
    loading = true;
    const requestGeneration = generation, requestedPages = pages;
    try {
      if (append) {
        const more = await options.api(`/api/git?offset=${data.next_offset}`);
        if (generation !== requestGeneration) return;
        const commits = [...data.commits, ...more.commits];
        data = {...more, commits}; pages++;
      } else {
        let fresh = await options.api("/api/git?offset=0");
        if (generation !== requestGeneration) return;
        for (let page = 1; fresh.enabled && page < requestedPages && fresh.next_offset !== null; page++) {
          const more = await options.api(`/api/git?offset=${fresh.next_offset}`);
          if (generation !== requestGeneration) return;
          fresh = {...more, commits:[...fresh.commits, ...more.commits]};
        }
        if (generation !== requestGeneration) return;
        data = fresh;
      }
      errorText = "";
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
      else { if (name === "disable") { selectedBranch = null; pages = 1; } await load(); if (generation === requestGeneration) options.toast(name === "enable" ? "已启用 Git 管理" : name === "disable" ? "已停用 Git 管理" : result.output === "" ? "fetch 完成" : result.output); }
    } catch (error) { if (generation === requestGeneration) errorText = error.message; }
    finally { if (generation === requestGeneration) { operation = null; render(); } }
  }
  function click(event) {
    const branch = event.target.closest("[data-git-branch]");
    if (branch !== null) { selectedBranch = selectedBranch === branch.dataset.gitBranch ? null : branch.dataset.gitBranch; render(); return; }
    const tabButton = event.target.closest("[data-git-tab]");
    if (tabButton !== null) { tab = tabButton.dataset.gitTab; render(); return; }
    const button = event.target.closest("[data-git-act]");
    if (button === null) return;
    const act = button.dataset.gitAct;
    if (act === "active") { activeOnly = !activeOnly; render(); }
    else if (act === "clear-selection") { selectedBranch = null; render(); }
    else if (act === "load-more") load(true);
    else if (act === "fetch") mutate("fetch", {});
    else if (act === "disable") { host.querySelector(".git-more").open = false; host.querySelector("#git-disable-dialog").showModal(); }
    else if (act === "cancel-disable") host.querySelector("#git-disable-dialog").close();
    else if (act === "confirm-disable") mutate("disable", {});
  }
  function submit(event) {
    if (event.target.id !== "git-enable-form") return;
    event.preventDefault();
    mutate("enable", {base:event.target.elements.base.value, stable:event.target.elements.stable.value});
  }
  function mount(element, settings) {
    if (host === element) return;
    unmount(); host = element; options = settings;
    host.addEventListener("click", click); host.addEventListener("submit", submit);
    render(); load(); timer = setInterval(() => load(), 30000);
  }
  function unmount() {
    generation++; clearInterval(timer);
    if (host !== null) { host.removeEventListener("click", click); host.removeEventListener("submit", submit); }
    host = null; options = null; data = null; loading = false; operation = null; errorText = ""; pages = 1; selectedBranch = null;
    expandedConflicts.clear();
  }
  return {mount, unmount};
})();
