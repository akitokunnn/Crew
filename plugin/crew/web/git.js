"use strict";
const CrewGit = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const DAY = 86400000, ROW = 34, GAP = 19;
  let host = null, options = null, data = null, timer = null, generation = 0;
  let tab = "graph", activeOnly = false, selectedBranch = null, loading = false, operation = null, errorText = "", pages = 1;
  let selectedRuntime = "", runtimeExplicit = false;
  const expandedConflicts = new Set();
  const commitStats = new Map();
  let detail = null, detailCloseTimer = null, hoverSuppressed = false, pointerX = null, pointerY = null;
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
  function mergeLabel(commit, includeTitle = false) {
    const pr = commit.pull_requests.find(item => item.merge_sha === commit.sha);
    if (pr !== undefined) return `合并 #${pr.number} ${includeTitle ? pr.title : pr.head} → ${pr.base}`;
    return `合并 ${commit.parent_branches.slice(1).join(" + ")} → ${commit.parent_branches[0]}`;
  }
  function recentMerge() {
    const merge = data.recent_merge;
    if (merge === null) return '<div class="git-recent-merge git-muted">最近一次合入：暂无合并提交</div>';
    const pr = merge.pull_requests.find(item => item.merge_sha === merge.sha);
    const label = pr === undefined ? `${merge.parent_branches.slice(1).join(" + ")} → ${merge.parent_branches[0]}` : `PR #${pr.number} ${pr.title}`;
    const text = pr === undefined ? esc(label) : `<a href="${esc(pr.url)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
    return `<div class="git-recent-merge" title="${esc(new Date(merge.time).toLocaleString("zh-CN", {hour12:false}))}">最近一次合入：${text} · ${esc(new Date(merge.time).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false}))}</div>`;
  }
  function commitTarget(target) {
    return target.closest("[data-git-sha], [data-git-node]");
  }
  function closeCommitDetail(suppressHover = false) {
    if (suppressHover) hoverSuppressed = true;
    clearTimeout(detailCloseTimer); detail = null;
    const popup = host.querySelector("#git-detail");
    if (popup !== null) popup.hidden = true;
  }
  function positionCommitDetail() {
    if (detail === null) return;
    const popup = host.querySelector("#git-detail"), box = popup.getBoundingClientRect(), anchor = detail.anchor;
    const left = anchor.right + box.width + 12 <= window.innerWidth ? anchor.right + 12 : anchor.left - box.width - 12;
    popup.style.left = `${Math.max(12, Math.min(left, window.innerWidth - box.width - 12))}px`;
    popup.style.top = `${Math.max(12, Math.min(anchor.top, window.innerHeight - box.height - 12))}px`;
  }
  function renderCommitDetail() {
    const popup = host.querySelector("#git-detail");
    if (detail === null) { popup.hidden = true; return; }
    const commit = detail.commit, stats = commitStats.get(`${detail.project}\0${commit.sha}`);
    const parents = commit.parents.length > 1 ? `<div class="git-detail-merge">${esc(mergeLabel(commit))}</div>` : "";
    const parentList = commit.parents.map((sha, index) => `<span><code title="${sha}">${sha.slice(0, 7)}</code> ${esc(commit.parent_branches[index])}</span>`).join("");
    let changes = "统计中…", files = "";
    if (stats.value !== null) {
      const value = stats.value;
      changes = `${value.file_count} 个文件，<span class="git-added">+${value.additions}</span> <span class="git-deleted">−${value.deletions}</span>`;
      files = `<ul class="git-detail-files">${value.files.slice(0, 10).map(file => `<li><code title="${esc(file.path)}">${esc(file.path)}</code>${file.binary ? '<span class="git-muted">二进制</span>' : `<span class="git-added">+${file.added}</span><span class="git-deleted">−${file.deleted}</span>`}</li>`).join("")}</ul>${value.file_count > 10 ? `<div class="git-muted">仅列出前 10 个文件，共 ${value.file_count} 个</div>` : ""}`;
    } else if (stats.error !== null) changes = `<span class="git-detail-error">统计失败：${esc(stats.error)}</span>`;
    popup.classList.toggle("is-pinned", detail.pinned);
    popup.dataset.gitDetailSha = commit.sha;
    popup.innerHTML = `<div class="git-detail-heading"><span>${detail.pinned ? "已固定 · 提交详情" : "提交详情 · 点击固定"}</span><button type="button" data-git-act="close-detail" aria-label="关闭提交详情">×</button></div><div class="git-detail-sha"><code>${commit.sha}</code><button type="button" data-git-act="copy-sha">复制</button></div><h3>${esc(commit.subject)}</h3>${commit.body === "" ? "" : `<div class="git-detail-body ${detail.expanded ? "" : "clamped"}">${esc(commit.body)}</div><button type="button" class="git-detail-expand" data-git-act="expand-body">${detail.expanded ? "收起正文" : "展开正文"}</button>`}<div class="git-detail-meta">${esc(commit.author)} · <time datetime="${esc(commit.time)}">${esc(new Date(commit.time).toLocaleString("zh-CN", {hour12:false}))}</time> · ${ago(commit.time)}</div>${parents}<div class="git-detail-parents"><span class="git-muted">父提交</span>${commit.parents.length === 0 ? '<span class="git-muted">无（初始提交）</span>' : parentList}</div>${commit.refs.length === 0 ? "" : `<div class="git-detail-refs">${commit.refs.map(name => `<span class="git-detail-ref" style="--branch-color:${branchColor(name)}">${esc(name)}</span>`).join("")}</div>`}${commit.pull_requests.length === 0 ? "" : `<div class="git-detail-prs">${commit.pull_requests.map(pr => `<a href="${esc(pr.url)}" target="_blank" rel="noreferrer"><strong>PR #${pr.number} ${esc(pr.title)}</strong><span>${({OPEN:"开放", MERGED:"已合并", CLOSED:"已关闭"})[pr.state]}${pr.draft ? " · 草稿" : ""}</span>${ci(pr.ci)}<span>↗</span></a>`).join("")}</div>`}<div class="git-detail-stats" aria-live="polite">${changes}</div>${files}`;
    popup.hidden = false;
    if (commit.body !== "") {
      const body = popup.querySelector(".git-detail-body");
      popup.querySelector("[data-git-act='expand-body']").hidden = !detail.expanded && body.scrollHeight <= body.clientHeight;
    }
    positionCommitDetail();
  }
  function showCommitDetail(target, pinned, point = null) {
    if (detail !== null && detail.pinned && !pinned) return;
    clearTimeout(detailCloseTimer);
    const sha = target.dataset.gitSha === undefined ? target.dataset.gitNode : target.dataset.gitSha;
    const key = `${options.project}\0${sha}`, same = detail !== null && detail.commit.sha === sha;
    const anchor = point === null ? target.getBoundingClientRect() : {left:point.clientX, right:point.clientX, top:point.clientY};
    detail = {commit:data.commits.find(commit => commit.sha === sha), project:options.project, anchor, pinned, expanded:same && detail.expanded};
    const cached = commitStats.get(key), missing = cached === undefined || cached.error !== null;
    if (missing) commitStats.set(key, {value:null, error:null});
    renderCommitDetail();
    if (missing) options.api(`/api/git/commit?project=${encodeURIComponent(options.project)}&sha=${sha}`).then(value => {
      commitStats.get(key).value = value;
      if (host !== null && detail !== null && `${detail.project}\0${detail.commit.sha}` === key) renderCommitDetail();
    }, error => {
      commitStats.get(key).error = error.message;
      if (host !== null && detail !== null && `${detail.project}\0${detail.commit.sha}` === key) renderCommitDetail();
    });
  }
  function hoverCommit(event) {
    if (hoverSuppressed) return;
    if (event.target.closest("#git-detail") !== null) { clearTimeout(detailCloseTimer); return; }
    const target = commitTarget(event.target);
    if (target !== null && (event.relatedTarget === null || !target.contains(event.relatedTarget))) showCommitDetail(target, false, event);
  }
  function moveCommitPointer(event) {
    const moved = event.clientX !== pointerX || event.clientY !== pointerY;
    pointerX = event.clientX; pointerY = event.clientY;
    if (!hoverSuppressed || !moved) return;
    hoverSuppressed = false;
    const target = commitTarget(event.target);
    if (target !== null) showCommitDetail(target, false, event);
  }
  function leaveCommit(event) {
    if (detail === null || detail.pinned) return;
    const target = commitTarget(event.target), popup = event.target.closest("#git-detail");
    if (target === null && popup === null) return;
    if (event.relatedTarget !== null && (target !== null && target.contains(event.relatedTarget) || popup !== null && popup.contains(event.relatedTarget))) return;
    clearTimeout(detailCloseTimer);
    detailCloseTimer = setTimeout(() => { if (host !== null && detail !== null && !detail.pinned) closeCommitDetail(); }, 150);
  }
  function dismissCommitDetail(event) {
    if (detail !== null && event.target.closest("#git-detail") === null && commitTarget(event.target) === null) closeCommitDetail();
  }
  function commitKey(event) {
    if (event.key === "Escape" && detail !== null) { closeCommitDetail(true); return; }
    if (event.key !== "Enter" || event.target.matches("[data-git-sha]") === false) return;
    event.preventDefault(); showCommitDetail(event.target, true);
  }
  function detailAction(event) {
    const button = event.target.closest("[data-git-act]");
    if (button === null) return false;
    const act = button.dataset.gitAct;
    if (act === "close-detail") closeCommitDetail(true);
    else if (act === "expand-body") { detail.pinned = true; detail.expanded = !detail.expanded; renderCommitDetail(); }
    else if (act === "copy-sha") navigator.clipboard.writeText(detail.commit.sha).then(() => options.toast("已复制完整 SHA"), error => options.toast(`复制失败：${error.message}`));
    else return false;
    return true;
  }
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
        const bend = Math.min(ROW / 2, (toY - fromY) / 2);
        lines += `<path d="M ${fromX} ${fromY} C ${fromX} ${fromY + bend} ${toX} ${toY - bend} ${toX} ${toY}" stroke="${color}" data-git-from="${commit.sha}" data-git-to="${parent}"/>`;
      }
      nodes += `<circle class="${commit.parents.length > 1 ? "git-merge-node" : ""}" cx="${fromX}" cy="${fromY}" r="${commit.parents.length > 1 ? 5.5 : 4}" fill="${commit.parents.length > 1 ? "var(--surface)" : color}" style="--node-color:${color}" data-git-node="${commit.sha}"/>`;
    }
    return {width, height, svg:`<svg class="git-lanes" viewBox="0 0 ${width} ${height}" style="width:calc(${width}px * var(--fs));height:calc(${height}px * var(--fs))" aria-label="提交父子关系图"><g class="git-edges">${lines}</g><g class="git-nodes">${nodes}</g></svg>`};
  }
  function graph() {
    const layout = graphLayout(), unique = new Set(selectedBranch === null ? [] : data.ref_unique_commits[selectedBranch]);
    const rows = data.commits.map(commit => {
      const locals = data.branches.filter(branch => commit.refs.includes(branch.name)), sessions = new Map();
      for (const branch of locals) for (const session of branch.sessions) sessions.set(session.session_id, session);
      const stableBoundary = data.stable !== null && commit.sha === data.stable.latest.sha;
      const marker = stableBoundary ? `<span class="git-stable-marker">${esc(data.stable.name)} 在这里 · ${esc(new Date(commit.time).toLocaleDateString("zh-CN"))}</span>` : "";
      const merge = commit.parents.length > 1 ? `<span class="git-merge-label" title="${esc(mergeLabel(commit, true))}">${esc(mergeLabel(commit))}</span>` : "";
      return `<div class="git-commit ${stableBoundary ? "git-stable-boundary" : ""} ${selectedBranch === null ? "" : unique.has(commit.sha) ? "is-highlighted" : "is-muted"}" data-git-sha="${commit.sha}" tabindex="0" aria-label="${esc(commit.subject)}，${esc(commit.author)}，${commit.sha.slice(0, 7)}" aria-haspopup="dialog"><div class="git-commit-main"><span class="git-refs">${commit.refs.map(name => branchTag(name, true)).join("")}${commit.pull_requests.map(prTag).join("")}${Array.from(sessions.values()).map(avatar).join("")}</span>${marker}${merge}<span class="git-subject" title="${esc(commit.subject)}">${esc(commit.subject)}</span></div><span class="git-commit-meta"><span class="git-commit-author">${esc(commit.author)}</span><span title="${esc(at(commit.time))}">${ago(commit.time)}</span><span class="git-sha">${commit.sha.slice(0, 7)}</span></span></div>`;
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
    const rows = [...data.branches].sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name)).map(branch => `<tr data-git-row="${esc(branch.name)}"><td><div class="git-table-name">${branchTag(branch.name)}${branch.sessions.map(avatar).join("")}</div><div class="git-statuses">${statusTags(branch)}</div>${originRelation(branch)}${branch.sessions.length === 0 ? "" : `<div class="git-session-names">${branch.sessions.map(session => esc(sessionName(session))).join("、")}</div>`}</td><td>${branch.worktrees.length === 0 ? '<span class="git-muted">—</span>' : branch.worktrees.map(item => `<div class="git-worktree" title="${esc(item.path)}"><code>${esc(item.path)}</code>${item.dirty ? '<span class="git-dirty-dot" title="有没提交的改动"></span>' : ""}</div>`).join("")}</td><td><span class="git-counts"><b class="${branch.ahead > 0 ? "ahead" : ""}">↑ ${branch.ahead}</b><b class="${branch.behind > 0 ? "behind" : ""}">↓ ${branch.behind}</b></span></td><td>${branch.conflicts.length === 0 ? '<span class="git-clean">✓ 干净</span>' : `<details class="git-conflicts" data-git-conflicts="${esc(branch.name)}" ${expandedConflicts.has(branch.name) ? "open" : ""}><summary>冲突 ${branch.conflicts.length} 个文件</summary><ul>${branch.conflicts.map(file => `<li><code>${esc(file)}</code></li>`).join("")}</ul></details>`}</td><td><div class="git-table-pr">${prsFor(branch.name).map(pr => `<span>${prTag(pr)}${ci(pr.ci)}<span class="git-pr-state">${({OPEN:"开放", MERGED:"已合并", CLOSED:"已关闭"})[pr.state]}</span></span>`).join("")}${prsFor(branch.name).length === 0 ? '<span class="git-muted">—</span>' : ""}</div></td><td><div class="git-last-time" title="${esc(at(branch.latest.time))}">${ago(branch.latest.time)} · ${esc(branch.latest.author)}</div><div class="git-last-subject" title="${esc(branch.latest.subject)}">${esc(branch.latest.subject)}</div><code class="git-short-sha">${branch.latest.sha.slice(0, 7)}</code></td></tr>`).join("");
    return `<div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支与负责会话</th><th>Worktree</th><th>领先 / 落后主干</th><th>合并检查</th><th>PR 与 CI</th><th>最后提交</th></tr></thead><tbody>${rows}</tbody></table></div>${data.remote_only.length === 0 ? "" : `<section class="git-remote-only"><h3>远端独有分支 <span>${data.remote_only.length}</span></h3><div class="git-table-scroll"><table class="git-branch-table"><thead><tr><th>分支</th><th>最后提交</th><th>PR 与 CI</th></tr></thead><tbody>${data.remote_only.map(branch => `<tr><td>${branchTag(branch.name)}</td><td><div class="git-last-subject">${esc(branch.latest.subject)}</div><span class="git-last-time">${esc(branch.latest.author)} · ${ago(branch.latest.time)} · ${branch.latest.sha.slice(0, 7)}</span></td><td>${prsFor(branch.name.replace(/^origin\//, "")).map(prTag).join("")}</td></tr>`).join("")}</tbody></table></div></section>`}`;
  }
  function enableCard() {
    const choices = data.choices, option = (name, selected) => `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`;
    const catalog = options.catalog?.() || [];
    if (!runtimeExplicit) selectedRuntime = options.defaultRuntime?.() || "";
    const available = catalog.some(item => item.id === selectedRuntime && item.available);
    const runtimeError = available ? "" : catalog.some(item => item.available) ? "所选执行工具当前不可用，请重新选择。" : "没有可用的执行工具，请安装并登录 Codex 或 Claude Code 后刷新。";
    return `<div class="page"><div class="git-enable-card"><span class="git-enable-icon">⑂</span><h1>启用 Git 管理</h1><p>Git 管理员会在独立 worktree 中统一推送和合并。选择日常合入的主干和发布用的稳定分支后，即可在这里查看请求、分支与 GitHub 状态。</p><form id="git-enable-form"><label for="git-base">主干分支 <span>日常合入</span></label><select id="git-base" name="base">${choices.branches.map(name => option(name, choices.base)).join("")}</select><label for="git-stable">稳定分支 <span>可留空</span></label><select id="git-stable" name="stable"><option value="" ${choices.stable === null ? "selected" : ""}>不设置稳定分支</option>${choices.branches.map(name => option(name, choices.stable)).join("")}</select><label for="git-runtime">管理员执行工具</label><select id="git-runtime" name="runtime"><option value="" disabled ${selectedRuntime === "" ? "selected" : ""}>请选择可用工具</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selectedRuntime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name)}${item.available ? "" : " · 未就绪"}</option>`).join("")}</select><p id="git-runtime-status" class="git-error" role="status" ${runtimeError ? "" : "hidden"}>${esc(runtimeError)}</p><button type="submit" class="btn primary" data-git-act="enable" ${operation !== null || choices.branches.length === 0 || !available ? "disabled" : ""}>${operation === "enable" ? "正在启用…" : "启用"}</button></form>${choices.branches.length === 0 ? '<p class="git-error">仓库没有本地分支，无法启用。</p>' : ""}${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</div></div>`;
  }
  function mergeRequests() {
    const labels = {open:"待处理", merging:"合并中", waiting_user:"等用户拍板", merged:"已合并", rejected:"已拒绝"};
    const active = request => ["open", "merging", "waiting_user"].includes(request.status);
    const requests = [...data.requests].sort((a, b) => Number(active(b)) - Number(active(a)) || b.id - a.id);
    const rows = requests.map(request => {
      const pr = request.pr_number === null || data.github === null ? undefined : data.github.pull_requests.find(item => item.number === request.pr_number);
      return `<tr data-git-request="${request.id}"><td><span class="git-request-status ${request.status}">${labels[request.status]}</span></td><td><div class="git-request-description"><span>#${request.id}</span>${esc(request.description)}</div>${request.note === null ? "" : `<div class="git-request-note">${esc(request.note)}</div>`}</td><td><code>${esc(request.branch)}</code></td><td><span title="${esc(request.session_id)}">${esc(request.requester_name)}</span><div class="git-request-time">${esc(at(request.created_at))}</div></td><td>${request.pr_number === null ? '<span class="git-muted">—</span>' : pr === undefined ? `<span class="git-request-pr">#${request.pr_number}</span>` : prTag(pr)}</td></tr>`;
    }).join("");
    return `<section class="git-requests" aria-labelledby="git-requests-title"><div class="git-requests-head"><h2 id="git-requests-title">合并请求 <span>${requests.filter(active).length} 件待处理</span></h2><span>完成后用 <code>crew git request</code> 交给管理员</span></div>${requests.length === 0 ? '<div class="git-requests-empty">还没有合并请求</div>' : `<div class="git-table-scroll"><table class="git-request-table"><thead><tr><th>状态</th><th>请求说明与结果</th><th>分支</th><th>提出者</th><th>PR</th></tr></thead><tbody>${rows}</tbody></table></div>`}</section>`;
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
  function render() {
    if (host === null) return;
    const scroll = host.querySelector(".git-page")?.scrollTop;
    if (data === null) { host.innerHTML = `<div id="git-view"><div class="head big"><div class="title">Git</div></div><div class="page"><p class="note git-loading">${errorText === "" ? "正在读取仓库…" : esc(errorText)}</p></div></div>`; return; }
    if (data.available === false) {
      detail = null;
      host.innerHTML = `<div id="git-view"><div class="head big git-head"><div><div class="title">Git</div><div class="sub">${esc(data.repository)}</div></div></div><div class="page"><section class="git-enable-card"><span class="git-enable-icon" aria-hidden="true">⑂</span><h1>Git 功能暂不可用</h1><p role="status">${esc(data.reason)}</p><p>此工作区的聊天、任务和其他功能照常使用。Git 分支、提交与 worktree 功能需要 Git 仓库。</p><button type="button" class="btn" data-git-act="check" ${loading ? "disabled" : ""}>${loading ? "正在检查…" : "刷新检查"}</button>${errorText === "" ? "" : `<p class="git-error" role="alert">${esc(errorText)}</p>`}</section></div></div>`;
      return;
    }
    if (!data.enabled) detail = null;
    const header = `<div class="head big git-head"><div><div class="title">${esc(data.repository)} <span class="git-heading-label">Git</span></div><div class="sub">${data.enabled ? "分支、提交与合并检查" : "项目 Git 管理"}</div></div>${data.enabled ? `<div class="right">${data.config.admin_session_id === null ? "" : `<button type="button" class="btn" data-act="leader" data-id="${esc(data.config.admin_session_id)}">和 git 管理员聊</button>`}<div class="git-fetch-meta">${data.last_fetch_at === null ? "尚未 fetch" : `上次 fetch · ${esc(at(data.last_fetch_at))}`}</div><button type="button" class="btn" data-git-act="fetch" ${operation !== null ? "disabled" : ""}>${operation === "fetch" ? "正在 fetch…" : "刷新（fetch）"}</button><details class="git-more"><summary aria-label="更多" title="更多">•••</summary><div><button type="button" data-git-act="disable">停用 Git 管理</button></div></details></div>` : ""}</div>`;
    host.innerHTML = `<div id="git-view">${header}${data.enabled ? `<div class="page git-page"><div class="git-page-in">${overview()}${mergeRequests()}${errorText === "" ? "" : `<pre class="git-error" role="alert">${esc(errorText)}</pre>`}${data.github === null ? `<div class="git-github-unavailable"><strong>GitHub 不可用</strong><pre>${esc(data.github_reason)}</pre></div>` : ""}<div class="git-tabs" role="tablist" aria-label="Git 视图"><button type="button" role="tab" id="git-graph-tab" aria-controls="git-panel" aria-selected="${tab === "graph"}" data-git-tab="graph" class="${tab === "graph" ? "on" : ""}">树状图 <span>${data.commits.length}</span></button><button type="button" role="tab" id="git-branches-tab" aria-controls="git-panel" aria-selected="${tab === "branches"}" data-git-tab="branches" class="${tab === "branches" ? "on" : ""}">分支 <span>${data.branches.length}</span></button></div><section id="git-panel" role="tabpanel" aria-labelledby="git-${tab}-tab">${tab === "graph" ? graph() : branchTable()}</section></div></div>` : enableCard()}<dialog id="git-disable-dialog" class="git-disable-dialog" aria-labelledby="git-disable-title"><h2 id="git-disable-title">停用 Git 管理？</h2><p>Git 管理员的组将关闭，此项目的 Git 管理配置将被删除。</p><div><button type="button" class="btn" data-git-act="cancel-disable">取消</button><button type="button" class="btn danger" data-git-act="confirm-disable" ${operation !== null ? "disabled" : ""}>确认停用</button></div></dialog><aside id="git-detail" class="git-detail" role="dialog" aria-label="提交详情" hidden></aside></div>`;
    if (scroll !== undefined && host.querySelector(".git-page") !== null) host.querySelector(".git-page").scrollTop = scroll;
    renderCommitDetail();
    for (const detail of host.querySelectorAll(".git-conflicts")) detail.addEventListener("toggle", () => { if (detail.open) expandedConflicts.add(detail.dataset.gitConflicts); else expandedConflicts.delete(detail.dataset.gitConflicts); });
  }
  async function load(append = false) {
    if (loading) return;
    loading = true;
    if (data?.available === false) render();
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
        for (let page = 1; fresh.available !== false && fresh.enabled && page < requestedPages && fresh.next_offset !== null; page++) {
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
      else { if (name === "disable") { selectedBranch = null; pages = 1; } await load(); if (name === "enable" || name === "disable") await options.onChanged(); if (generation === requestGeneration) options.toast(name === "enable" ? "已启用 Git 管理" : name === "disable" ? "已停用 Git 管理" : result.output === "" ? "fetch 完成" : result.output); }
    } catch (error) { if (generation === requestGeneration) errorText = error.message; }
    finally { if (generation === requestGeneration) { operation = null; render(); } }
  }
  function click(event) {
    if (detailAction(event)) return;
    if (event.target.closest("#git-detail") !== null) { detail.pinned = true; renderCommitDetail(); return; }
    const branch = event.target.closest("[data-git-branch]");
    if (branch !== null) { selectedBranch = selectedBranch === branch.dataset.gitBranch ? null : branch.dataset.gitBranch; render(); return; }
    const tabButton = event.target.closest("[data-git-tab]");
    if (tabButton !== null) { tab = tabButton.dataset.gitTab; render(); return; }
    const button = event.target.closest("[data-git-act]");
    if (button === null) { const target = commitTarget(event.target); if (target !== null) showCommitDetail(target, true, event); return; }
    const act = button.dataset.gitAct;
    if (act === "check") load();
    else if (act === "active") { activeOnly = !activeOnly; render(); }
    else if (act === "clear-selection") { selectedBranch = null; render(); }
    else if (act === "load-more") load(true);
    else if (act === "fetch") mutate("fetch", {});
    else if (act === "disable") { host.querySelector(".git-more").open = false; host.querySelector("#git-disable-dialog").showModal(); }
    else if (act === "cancel-disable") host.querySelector("#git-disable-dialog").close();
    else if (act === "confirm-disable") mutate("disable", {});
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
    if (host === element) return;
    unmount(); host = element; options = settings;
    host.addEventListener("click", click); host.addEventListener("submit", submit); host.addEventListener("change", change);
    host.addEventListener("pointerover", hoverCommit); host.addEventListener("pointerout", leaveCommit);
    document.addEventListener("pointerdown", dismissCommitDetail); document.addEventListener("keydown", commitKey);
    document.addEventListener("pointermove", moveCommitPointer);
    window.addEventListener("resize", positionCommitDetail);
    render(); load(); timer = setInterval(() => { if (data?.available !== false) load(); }, 30000);
  }
  function unmount() {
    generation++; clearInterval(timer); clearTimeout(detailCloseTimer); detail = null; hoverSuppressed = false; pointerX = null; pointerY = null;
    if (host !== null) { host.removeEventListener("click", click); host.removeEventListener("submit", submit); host.removeEventListener("change", change); host.removeEventListener("pointerover", hoverCommit); host.removeEventListener("pointerout", leaveCommit); }
    document.removeEventListener("pointerdown", dismissCommitDetail); document.removeEventListener("keydown", commitKey);
    document.removeEventListener("pointermove", moveCommitPointer);
    window.removeEventListener("resize", positionCommitDetail);
    host = null; options = null; data = null; loading = false; operation = null; errorText = ""; pages = 1; selectedBranch = null; selectedRuntime = ""; runtimeExplicit = false;
    expandedConflicts.clear();
  }
  return {mount, unmount};
})();
