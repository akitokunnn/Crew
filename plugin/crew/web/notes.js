"use strict";
// Idea wall: one page over /api/personal/notes and /api/personal/tags.
// Every write goes to the server and the wall re-reads; nothing is guessed locally.
(() => {
  const M = window.CrewNotesModel;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const SNOOZE_DAYS = [3, 7, 30];
  const DRAFT_KEY = "crew.notes.capture";

  let notes = [], tags = [], projects = [], allProjects = [], staleDays = 14, dupes = new Map();
  let loaded = false, view = M.parseHash(location.hash), selectMode = false, review = null, menu = null, toastTimer = null;
  let captureProject = view.ws, capturePending = null, suggest = null;
  const selected = new Set(), expanded = new Set(), starting = new Map();

  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  function theme() { let selectedTheme = "system"; try { selectedTheme = localStorage.getItem("crew.theme") || selectedTheme; } catch {} document.documentElement.dataset.theme = selectedTheme === "system" ? (systemTheme.matches ? "dark" : "light") : selectedTheme; }
  theme(); systemTheme.addEventListener("change", theme); window.addEventListener("storage", theme);

  async function api(path, body) {
    const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: {"X-Crew": "1", ...(body === undefined ? {} : {"Content-Type": "application/json"})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const value = await response.json();
    if (!response.ok) { const error = Error(value.error || `请求失败 (${response.status})`); error.status = response.status; error.body = value; throw error; }
    return value;
  }
  const write = (action, data) => api("/api/personal/notes", {action, data, request_id: window.CrewBrowser.requestId()});
  const tagWrite = (action, data) => api("/api/personal/tags", {action, data, request_id: window.CrewBrowser.requestId()});

  const byId = id => notes.find(note => note.id === id);
  const tagInfo = name => tags.find(tag => tag.name === name);
  const projectName = name => { const found = allProjects.find(item => item.name === name); return found ? found.display_name || found.name : name; };
  const leaderUrl = leader => `/?project=${encodeURIComponent(leader.project)}#leader=${encodeURIComponent(leader.session_id)}`;
  const chatUrl = note => `/assistant.html#note=${encodeURIComponent(note.id)}`;
  const versions = ids => Object.fromEntries(ids.map(id => [id, byId(id).version]));
  const day = iso => { const date = new Date(iso); return `${date.getMonth() + 1}月${date.getDate()}日`; };
  const stamp = iso => { const date = new Date(iso); return `${day(iso)} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`; };

  function toast(message, action) {
    const node = $("nw-toast");
    node.replaceChildren(document.createTextNode(message));
    if (action) { const button = document.createElement("button"); button.type = "button"; button.textContent = action.label; button.onclick = action.run; node.append(button); }
    node.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove("show"), 6000);
  }

  // ---- data ---------------------------------------------------------------
  async function refresh() {
    const [list, registry] = await Promise.all([api("/api/personal/notes?include_archived=1"), api("/api/personal/tags")]);
    notes = list.notes; staleDays = list.stale_days; tags = M.orderTags(registry.tags); dupes = M.duplicates(notes);
    for (const id of [...selected]) if (byId(id)?.state !== "open") selected.delete(id);
    loaded = true; $("nw-error").hidden = true;
  }
  async function reload() {
    try { await refresh(); } catch (error) { showError(error.message); }
    render();
  }
  async function boot() {
    $("nw-error").hidden = true;
    try {
      const listing = await api("/api/projects");
      allProjects = listing.projects; projects = listing.projects.filter(item => !item.read_only);
      await refresh();
    } catch (error) { showError(error.message); }
    render();
  }
  function showError(message) {
    const node = $("nw-error"); node.replaceChildren(); node.hidden = false;
    const text = document.createElement("span"); text.textContent = `想法读取失败：${message}`; node.append(text);
    const retry = document.createElement("button"); retry.type = "button"; retry.className = "btn"; retry.textContent = "重试"; retry.onclick = () => { void boot(); }; node.append(retry);
  }
  // Runs one write, then re-reads. A version conflict also re-reads so the
  // next attempt starts from what the server holds; the error goes to the caller.
  async function commit(run) {
    let result;
    try { result = await run(); }
    catch (error) { if (error.status === 409) await reload(); throw error; }
    await reload();
    return result;
  }
  // Buttons without a form report through the toast with the server's reason.
  async function attempt(label, run, done) {
    let result;
    try { result = await commit(run); }
    catch (error) { toast(`${label}失败：${error.message}`); return; }
    done?.(result);
  }

  // ---- view state -----------------------------------------------------------
  function setView(patch) {
    const wasWs = view.ws;
    view = {...view, ...patch};
    if (view.ws !== wasWs) captureProject = view.ws;
    history.replaceState(null, "", location.pathname + location.search + M.toHash(view));
    render();
  }
  function toggleFilterTag(name) {
    const tagsNow = view.tags.includes(name) ? view.tags.filter(tag => tag !== name) : [...view.tags, name];
    setView({tags: tagsNow, untagged: false});
  }
  window.addEventListener("hashchange", () => { view = M.parseHash(location.hash); captureProject = view.ws; render(); });

  // ---- rendering ------------------------------------------------------------
  function tagChip(name, act = "filter-tag") {
    const info = tagInfo(name), on = view.tags.includes(name);
    return `<button type="button" class="nw-tag tag-${esc(info.color)}" data-act="${act}" data-tag="${esc(name)}" aria-pressed="${on}" title="按「${esc(name)}」筛选">${esc(name)}</button>`;
  }
  function source(note) {
    if (note.merged_from.length) return "合并而来";
    return note.source.type === "human" ? "手记" : "助手代记";
  }
  function statusParts(note, now) {
    const parts = [];
    if (note.discussions.count) parts.push(`<a class="text-link" href="/assistant.html">讨论过 ${note.discussions.count} 次</a>`);
    if (note.summaries.length) parts.push(`<button type="button" class="text-link" data-act="summaries">结论 ${note.summaries.length} 条</button>`);
    if (note.leader?.status === "completed") parts.push(`已转任务 · <a class="text-link" href="${esc(leaderUrl(note.leader))}">${esc(projectName(note.leader.project))}</a>`);
    else if (note.leader) parts.push(`<span class="nw-warn">转任务结果待核实</span>`);
    if (note.leader?.error) parts.push(`<span class="nw-warn">${esc(note.leader.error)}</span>`);
    if (note.snoozed_until && Date.parse(note.snoozed_until) > now) parts.push(`暂缓到 ${day(note.snoozed_until)}`);
    if (note.state === "open" && note.stale) parts.push(`<span class="nw-warn">沉睡 ${M.daysSince(new Date(M.idleSince(note)).toISOString(), now)} 天</span>`);
    if (note.merged_into) { const primary = byId(note.merged_into); parts.push(`已并入「${esc(primary ? M.title(primary) : note.merged_into)}」`); }
    if (note.merged_from.length) parts.push(`合并了 ${note.merged_from.length} 条`);
    return parts.join('<span class="nw-dot" aria-hidden="true">·</span>');
  }
  function actions(note) {
    const more = `<button type="button" class="icon-btn nw-more-btn" data-act="menu" aria-haspopup="menu" aria-expanded="false" aria-label="更多操作">⋯</button>`;
    if (note.state !== "open") {
      const main = note.merged_into ? `<button type="button" class="btn sm" data-act="unmerge">撤销合并</button>`
        : note.stage === "promoted" ? (note.leader?.session_id ? `<a class="btn sm" href="${esc(leaderUrl(note.leader))}">打开任务对话</a>` : "")
        : `<button type="button" class="btn sm" data-act="restore">恢复</button>`;
      return main + more;
    }
    const chat = `<a class="btn primary sm" href="${esc(chatUrl(note))}" data-act="chat">去聊聊</a>`;
    if (note.leader && note.leader.status !== "completed") return chat + `<button type="button" class="btn sm" data-act="recover">核实转任务</button>` + more;
    if (note.stale) return chat + `<button type="button" class="btn sm" data-act="keep">保留</button><button type="button" class="btn sm" data-act="archive">归档</button>` + more;
    return chat + `<button type="button" class="btn sm" data-act="promote">转任务</button>` + more;
  }
  function card(note, now) {
    const title = M.title(note), text = M.body(note), status = statusParts(note, now), isSelected = selected.has(note.id), open = expanded.has(note.id);
    const classes = ["nw-card", note.state === "open" && note.stale ? "stale" : "", isSelected ? "selected" : "", note.pinned_at ? "pinned" : ""].filter(Boolean).join(" ");
    return `<article class="${classes}" data-id="${esc(note.id)}" tabindex="0" aria-label="想法：${esc(title)}">`
      + (note.state === "open" ? `<input type="checkbox" class="nw-check" data-act="select" ${isSelected ? "checked" : ""} aria-label="选择「${esc(title)}」">` : "")
      + `<div class="nw-card-top"><h3 class="nw-title">${note.pinned_at ? '<span class="nw-pin" aria-label="已置顶">▲</span>' : ""}${esc(title)}</h3>${note.state === "open" && dupes.has(note.id) ? `<button type="button" class="nw-dup" data-act="merge-dup" title="和 ${dupes.get(note.id).length} 条想法很像，点此合并">可能重复</button>` : ""}</div>`
      + (text ? `<p class="nw-body${open ? " open" : ""}">${esc(text)}</p><button type="button" class="text-link nw-expand" data-act="expand" hidden>${open ? "收起" : "展开全文"}</button>` : "")
      + (note.tags.length ? `<div class="nw-tags">${note.tags.map(name => tagChip(name)).join("")}</div>` : "")
      + `<p class="nw-meta">${esc(note.project === null ? "全局" : projectName(note.project))}<span class="nw-dot" aria-hidden="true">·</span>${source(note)}<span class="nw-dot" aria-hidden="true">·</span><time datetime="${esc(note.updated_at)}" title="${esc(stamp(note.updated_at))}">${M.relative(note.updated_at, now)}</time></p>`
      + (status ? `<p class="nw-state">${status}</p>` : "")
      + `<div class="nw-acts">${actions(note)}</div></article>`;
  }
  function emptyWall(total) {
    if (!notes.length) return `<div class="nw-empty"><h2>还没有想法</h2><p>在上面写下第一条，Enter 记下。输入 # 可以顺手打标签。</p></div>`;
    const filtered = view.tags.length || view.untagged || view.ws !== null || view.global;
    const words = {open: "没有正在进行的想法。", stale: "没有需要回顾的想法，墙上的都还新鲜。", promoted: "还没有转成任务的想法。", archived: "还没有归档的想法。"};
    return `<div class="nw-empty"><h2>${filtered && total ? "没有符合筛选的想法" : words[view.view]}</h2>${filtered ? `<p><button type="button" class="btn" data-act="clear-filters">清除筛选</button></p>` : ""}</div>`;
  }
  function renderWall() {
    const wall = $("nw-wall");
    if (!loaded) { wall.innerHTML = $("nw-error").hidden ? `<p class="nw-loading" role="status">正在读取想法…</p>` : ""; return; }
    const focus = document.activeElement?.closest?.("#nw-wall [data-id]");
    const focusKey = focus ? [focus.dataset.id, document.activeElement.dataset.act || ""] : null;
    const now = Date.now(), visible = M.filter(notes, view);
    if (!visible.length) { wall.innerHTML = emptyWall(notes.filter(note => M.inView(note, view.view)).length); return; }
    wall.innerHTML = M.group(visible, view, now).map(section => `<section class="nw-section ${section.tone}" aria-labelledby="nw-band-${section.key}"><h2 class="nw-band" id="nw-band-${section.key}">${section.label}<span class="nw-count">${section.items.length}</span>${section.key === "stale" ? `<small>超过 ${staleDays} 天没动</small>` : ""}</h2><div class="nw-cards">${section.items.map(note => card(note, now)).join("")}</div></section>`).join("");
    for (const body of wall.querySelectorAll(".nw-body")) {
      const toggle = body.nextElementSibling;
      toggle.hidden = !(body.classList.contains("open") || body.scrollHeight > body.clientHeight + 1);
    }
    if (focusKey) {
      const node = wall.querySelector(`[data-id="${CSS.escape(focusKey[0])}"]`);
      (focusKey[1] && node?.querySelector(`[data-act="${focusKey[1]}"]`) || node)?.focus({preventScroll: true});
    }
  }
  function renderFilters() {
    const scope = view.ws !== null ? `ws:${view.ws}` : view.global ? "global" : "";
    const viewNames = {open: "全部想法", stale: "需要回顾", promoted: "已转任务", archived: "已归档"};
    const filtered = view.tags.length || view.untagged || view.ws !== null || view.global;
    $("nw-filters").innerHTML = `<div class="nw-filter-tags">${view.tags.map(name => tagInfo(name) ? `<span class="nw-tag tag-${esc(tagInfo(name).color)} nw-tag-on">${esc(name)}<button type="button" data-act="filter-tag" data-tag="${esc(name)}" aria-label="移除标签筛选「${esc(name)}」">×</button></span>` : `<span class="nw-tag nw-tag-missing">${esc(name)}<button type="button" data-act="filter-tag" data-tag="${esc(name)}" aria-label="移除标签筛选「${esc(name)}」">×</button></span>`).join("")}<button type="button" class="btn sm ghost-line" data-act="tag-panel" aria-keyshortcuts="T">＋ 标签</button></div>`
      + `<div class="nw-seg" role="radiogroup" aria-label="多个标签时"><button type="button" role="radio" aria-checked="${view.mode === "all"}" data-act="mode" data-value="all">全部满足</button><button type="button" role="radio" aria-checked="${view.mode === "any"}" data-act="mode" data-value="any">任一</button></div>`
      + `<button type="button" class="btn sm ghost-line" data-act="untagged" aria-pressed="${view.untagged}">无标签</button>`
      + `<select data-act="scope" aria-label="工作区"><option value="" ${scope === "" ? "selected" : ""}>全部工作区</option><option value="global" ${scope === "global" ? "selected" : ""}>全局</option>${allProjects.map(item => `<option value="ws:${esc(item.name)}" ${scope === `ws:${item.name}` ? "selected" : ""}>${esc(item.display_name || item.name)}</option>`).join("")}${view.ws !== null && !allProjects.some(item => item.name === view.ws) ? `<option value="ws:${esc(view.ws)}" selected>${esc(view.ws)}</option>` : ""}</select>`
      + `<select data-act="sort" aria-label="排序"><option value="updated" ${view.sort === "updated" ? "selected" : ""}>最近更新</option><option value="created" ${view.sort === "created" ? "selected" : ""}>创建时间</option><option value="discussed" ${view.sort === "discussed" ? "selected" : ""}>最近讨论</option></select>`
      + `<div class="nw-seg" role="radiogroup" aria-label="分组"><button type="button" role="radio" aria-checked="${view.group === "time"}" data-act="group" data-value="time">按时间</button><button type="button" role="radio" aria-checked="${view.group === "stage"}" data-act="group" data-value="stage">按阶段</button></div>`
      + `<select class="nw-narrow-only" data-act="view" aria-label="查看">${Object.entries(viewNames).map(([key, label]) => `<option value="${key}" ${view.view === key ? "selected" : ""}>${label}</option>`).join("")}</select>`
      + (filtered ? `<button type="button" class="text-link" data-act="clear-filters">清除筛选</button>` : "")
      + (view.view !== "open" ? `<span class="nw-viewing">正在看：${viewNames[view.view]}<button type="button" class="text-link" data-act="view-open">返回全部想法</button></span>` : "");
  }
  function renderSide() {
    // No counts until the notes are read: an empty rail would read as "no ideas".
    if (!loaded) { $("nw-side").innerHTML = ""; return; }
    const count = M.counts(notes);
    const row = (attrs, label, number, extra = "") => `<button type="button" class="nw-side-row" ${attrs}>${extra}<span class="nw-side-name">${label}</span><span class="nw-side-count">${number}</span></button>`;
    $("nw-side").innerHTML = `<h2>标签</h2><div class="nw-side-list">${tags.length ? tags.map(tag => row(`data-act="filter-tag" data-tag="${esc(tag.name)}" aria-pressed="${view.tags.includes(tag.name)}"`, esc(tag.name), count.tags.get(tag.name) || 0, `<i class="nw-swatch tag-${esc(tag.color)}" aria-hidden="true"></i>${tag.pinned ? '<span class="nw-pin" aria-label="置顶">▲</span>' : ""}`)).join("") : '<p class="nw-quiet">还没有标签。记想法时输入 # 就能加。</p>'}${row(`data-act="untagged" aria-pressed="${view.untagged}"`, "无标签", count.untagged, '<i class="nw-swatch nw-swatch-none" aria-hidden="true"></i>')}</div>`
      + `<hr><div class="nw-side-list">${row(`data-act="view" data-value="open" aria-pressed="${view.view === "open"}"`, "全部想法", count.open)}${row(`data-act="view" data-value="stale" aria-pressed="${view.view === "stale"}"`, "需要回顾", count.stale)}${row(`data-act="view" data-value="promoted" aria-pressed="${view.view === "promoted"}"`, "已转任务", count.promoted)}${row(`data-act="view" data-value="archived" aria-pressed="${view.view === "archived"}"`, "已归档", count.archived)}</div>`
      + `<hr><button type="button" class="btn sm" data-act="manage-tags">管理标签</button><p class="nw-keys">快捷键：R 回顾 · T 标签 · 1–9 置顶标签</p>`;
  }
  function renderCaptureExtra() {
    const parts = [];
    if (captureProject !== null) parts.push(`<span class="nw-chip">工作区：${esc(projectName(captureProject))}<button type="button" data-act="capture-global" aria-label="改为全局想法">×</button></span>`);
    const auto = view.untagged ? [] : view.tags;
    if (auto.length) parts.push(`<span class="nw-auto">会自动带上 ${auto.map(name => `#${esc(name)}`).join(" ")}</span>`);
    $("nw-capture-extra").innerHTML = parts.join("");
  }
  function renderBulk() {
    const bar = $("nw-bulk");
    bar.hidden = selected.size === 0;
    document.body.classList.toggle("nw-selecting", selectMode || selected.size > 0);
    $("nw-select-mode").setAttribute("aria-pressed", String(selectMode));
    if (!selected.size) { bar.innerHTML = ""; return; }
    bar.innerHTML = `<span>已选 <b>${selected.size}</b> 条</span><button type="button" class="btn sm" data-act="bulk-tags">打标签</button><button type="button" class="btn sm" data-act="bulk-project">改工作区</button><button type="button" class="btn sm" data-act="bulk-merge" ${selected.size < 2 ? "disabled" : ""}>合并</button><button type="button" class="btn sm" data-act="bulk-archive">归档</button><button type="button" class="btn sm ghost" data-act="bulk-clear">取消选择</button>`;
  }
  function render() {
    const count = M.counts(notes);
    $("nw-review").innerHTML = `回顾${count.stale ? `<b class="nw-badge">${count.stale}</b>` : ""}`;
    $("nw-review").title = count.stale ? `${count.stale} 条想法超过 ${staleDays} 天没动（R）` : "逐张回顾当前的想法（R）";
    renderFilters(); renderSide(); renderCaptureExtra(); renderWall(); renderBulk();
  }

  // ---- dialogs ----------------------------------------------------------------
  function dialog(className, html, label) {
    const node = document.createElement("dialog");
    node.className = `nw-dialog ${className}`; node.setAttribute("aria-label", label); node.innerHTML = html;
    document.body.append(node); node.showModal();
    node.addEventListener("close", () => node.remove());
    node.addEventListener("click", event => { if (event.target.closest("[data-close]")) node.close(); });
    // Esc always closes, even from a search box that would only clear itself.
    node.addEventListener("keydown", event => { if (event.key === "Escape" && !event.isComposing) { event.preventDefault(); node.close(); } });
    return node;
  }
  const head = (title, sub = "") => `<header><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ""}</div><button type="button" class="icon-btn" data-close aria-label="关闭">×</button></header>`;
  function confirmDialog(title, message, ok, danger = false) {
    return new Promise(resolve => {
      const node = dialog("nw-confirm", `${head(title)}<p class="nw-confirm-text">${message}</p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="button" class="btn ${danger ? "danger" : "primary"}" data-ok>${ok}</button></div>`, title);
      let answer = false;
      node.querySelector("[data-ok]").onclick = () => { answer = true; node.close(); };
      node.addEventListener("close", () => resolve(answer));
      node.querySelector("[data-ok]").focus();
    });
  }
  const projectOptions = (current, withGlobal) => (withGlobal ? `<option value="" ${current === null ? "selected" : ""}>全局（不属于工作区）</option>` : `<option value="" disabled ${projects.some(item => item.name === current) ? "" : "selected"}>选择工作区</option>`)
    + projects.map(item => `<option value="${esc(item.name)}" ${item.name === current ? "selected" : ""}>${esc(item.display_name || item.name)}</option>`).join("");
  const formStatus = (node, text) => { node.querySelector("[role=status]").textContent = text; };
  function lockForm(node, value) { for (const control of node.querySelectorAll("button,input,textarea,select")) if (!control.hasAttribute("data-close")) control.disabled = value; }

  function editDialog(note) {
    const node = dialog("nw-edit", `${head("编辑想法", "最初记录始终保留，可以在 ⋯ 里查看。")}<form><label>标题<input name="title" maxlength="80" placeholder="可不填，不填就取正文第一行" value="${esc(note.title || "")}"></label><label>内容<textarea name="text" rows="8" required>${esc(note.text)}</textarea></label><label>标签<input name="tags" value="${esc(note.tags.join(" "))}" placeholder="用空格或逗号分隔"></label><label>工作区<select name="project">${projectOptions(note.project, true)}</select></label><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="submit" class="btn primary">保存</button></div></form>`, "编辑想法");
    const form = node.querySelector("form");
    form.onsubmit = async event => {
      event.preventDefault();
      const text = form.text.value.trim();
      if (!text) { formStatus(node, "内容不能为空。"); return; }
      lockForm(node, true); formStatus(node, "正在保存…");
      try {
        await commit(() => write("update", {note_id: note.id, expected_version: note.version, text, title: form.title.value.trim() || null,
          tags: [...new Set(form.tags.value.split(/[\s,，]+/).filter(Boolean))], project: form.project.value || null}));
        node.close(); toast("已保存。");
      } catch (error) { formStatus(node, error.message); lockForm(node, false); }
    };
    form.text.focus();
  }

  function promoteDialog(note, after) {
    const node = dialog("nw-promote", `${head("转任务", "在所选工作区新建一个 leader 讨论，带上这条想法。转任务后想法从墙上移到“已转任务”。")}<form><p class="nw-quote">${esc(M.title(note))}</p><label>工作区<select name="project" required>${projectOptions(note.project, false)}</select></label><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="submit" class="btn primary">转任务</button></div></form>`, "转任务");
    const form = node.querySelector("form");
    if (!projects.length) formStatus(node, "还没有可用的工作区。先在工作区页打开一个项目文件夹。");
    form.onsubmit = async event => {
      event.preventDefault();
      const project = form.project.value;
      if (!project) { formStatus(node, "先选择工作区。"); return; }
      if (!starting.has(note.id)) starting.set(note.id, window.CrewBrowser.requestId());
      lockForm(node, true); formStatus(node, "正在新建 leader 讨论…");
      try {
        const result = await commit(() => api("/api/personal/notes/start", {note_id: note.id, project, request_id: starting.get(note.id)}));
        starting.delete(note.id); node.close();
        toast(`已转任务：在「${projectName(result.project)}」新建了 leader 讨论。`, {label: "打开对话", run: () => { location.href = leaderUrl({project: result.project, session_id: result.session_id}); }});
        after?.();
      } catch (error) { formStatus(node, error.message); lockForm(node, false); }
    };
    form.project.focus();
  }

  async function recoverDialog(note) {
    const node = dialog("nw-recover", `${head("核实转任务", "上次转任务没有确认结果。这里只建立关联，不发送想法，也不新建会话。")}<div data-body><p class="nw-loading" role="status">正在查找已有对话…</p></div>`, "核实转任务");
    const bodyNode = node.querySelector("[data-body]");
    let result;
    try { result = await api(`/api/personal/notes/recovery?note_id=${encodeURIComponent(note.id)}`); }
    catch (error) { bodyNode.innerHTML = `<p class="nw-form-error" role="alert">${esc(error.message)}</p>`; return; }
    if (!result.leaders.length) { bodyNode.innerHTML = `<p>未找到可关联的对话。该次创建结果仍需核实，暂不重复创建。</p>`; return; }
    bodyNode.innerHTML = `<form><label>选择你核实过的原讨论<select name="session">${result.leaders.map(person => `<option value="${esc(person.session_id)}">${esc(person.crew_name)} · ${esc(person.task || person.session_id.slice(0, 8))}</option>`).join("")}</select></label><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="submit" class="btn primary">确认这是原讨论</button></div></form>`;
    const form = bodyNode.querySelector("form");
    form.onsubmit = async event => {
      event.preventDefault();
      const session_id = form.session.value;
      lockForm(node, true);
      try {
        await commit(() => api("/api/personal/notes/recovery", {note_id: note.id, session_id, request_id: `note-recovery:${note.id}:${session_id}`, reason: "用户在想法墙中核实并选择了原讨论"}));
        node.close(); toast("已关联原讨论，没有重新发送内容。");
      } catch (error) { formStatus(node, error.message); lockForm(node, false); }
    };
  }

  function originalDialog(note) {
    dialog("nw-original", `${head("最初记录", `记于 ${stamp(note.created_at)}`)}<pre class="nw-pre">${esc(note.original_text)}</pre>${note.merged_from.length ? `<h3>合并来源</h3><ul class="nw-plain">${note.merged_from.map(id => { const item = byId(id); return `<li>${esc(item ? M.title(item) : id)}</li>`; }).join("")}</ul>` : ""}<div class="nw-dialog-acts"><button type="button" class="btn" data-close>关闭</button></div>`, "最初记录");
  }

  function summariesDialog(note) {
    const node = dialog("nw-summaries", `${head("讨论结论", `结论只追加，不改你的原文。${note.discussions.count ? `已讨论 ${note.discussions.count} 次。` : ""}`)}<div class="nw-summary-list">${note.summaries.length ? note.summaries.map(item => `<article class="nw-summary"><p>${esc(item.text)}</p><small>${item.source.type === "assistant" ? "助手" : "你"} · ${esc(stamp(item.at))}</small></article>`).join("") : '<p class="nw-quiet">还没有讨论结论。去聊聊后，助手会把结论追加到这里；你也可以自己写一条。</p>'}</div><form><label>追加一条结论<textarea name="text" rows="3" required placeholder="比如：先做 A，B 等下个版本"></textarea></label><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts">${note.discussions.count ? `<a class="btn ghost" href="/assistant.html">打开助手对话</a>` : ""}<button type="button" class="btn ghost" data-close>关闭</button><button type="submit" class="btn primary">追加</button></div></form>`, "讨论结论");
    const form = node.querySelector("form");
    form.onsubmit = async event => {
      event.preventDefault();
      const text = form.text.value.trim();
      if (!text) return;
      lockForm(node, true);
      try { await commit(() => write("annotate", {note_id: note.id, text})); node.close(); toast("已追加讨论结论。"); }
      catch (error) { formStatus(node, error.message); lockForm(node, false); }
    };
  }

  // Merge: choose which cards go in and which one stays as the primary.
  function mergeDialog(seedIds, after) {
    const seeds = seedIds.filter(id => byId(id)?.state === "open");
    const included = new Set(seeds);
    let primary = seeds[0], query = "";
    const node = dialog("nw-merge", `${head("合并想法", "被并入的卡片原文追加到主卡末尾，自己归档；标签取并集。可以在“已归档”里撤销合并。")}<form><input type="search" name="query" placeholder="搜索其他想法加入合并" aria-label="搜索想法"><div class="nw-merge-list" role="group" aria-label="参与合并的想法"></div><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="submit" class="btn primary">合并</button></div></form>`, "合并想法");
    const form = node.querySelector("form"), list = node.querySelector(".nw-merge-list");
    function paint() {
      const candidates = notes.filter(note => note.state === "open" && (included.has(note.id) || (query && (M.title(note) + note.text).toLowerCase().includes(query.toLowerCase()))));
      const ordered = [...seeds.map(byId), ...candidates.filter(note => !seeds.includes(note.id))].filter((note, index, all) => all.indexOf(note) === index);
      list.innerHTML = ordered.map(note => `<div class="nw-merge-row${included.has(note.id) ? " in" : ""}"><label class="nw-merge-pick"><input type="checkbox" data-include="${esc(note.id)}" ${included.has(note.id) ? "checked" : ""}><span><b>${esc(M.title(note))}</b><small>${esc(note.text.slice(0, 120))}</small></span></label><label class="nw-merge-primary"><input type="radio" name="primary" value="${esc(note.id)}" ${primary === note.id ? "checked" : ""} ${included.has(note.id) ? "" : "disabled"}>主卡</label></div>`).join("") || '<p class="nw-quiet">搜索想法，把它们加进来。</p>';
      const count = included.size - 1;
      form.querySelector("[type=submit]").textContent = count > 0 ? `把 ${count} 条并入主卡` : "合并";
      form.querySelector("[type=submit]").disabled = count < 1;
    }
    list.addEventListener("change", event => {
      const id = event.target.dataset.include;
      if (id) { if (event.target.checked) included.add(id); else included.delete(id); if (!included.has(primary)) primary = [...included][0]; }
      if (event.target.name === "primary") primary = event.target.value;
      paint();
    });
    form.query.oninput = () => { query = form.query.value.trim(); paint(); };
    form.onsubmit = async event => {
      event.preventDefault();
      const others = [...included].filter(id => id !== primary);
      lockForm(node, true); formStatus(node, "正在合并…");
      try {
        await commit(() => write("merge", {primary_id: primary, note_ids: others, expected_versions: versions([primary, ...others])}));
        for (const id of others) selected.delete(id);
        node.close(); render(); toast(`已把 ${others.length} 条并入「${M.title(byId(primary))}」。`); after?.();
      } catch (error) { formStatus(node, error.message); lockForm(node, false); paint(); }
    };
    paint(); form.query.focus();
  }

  // Tag panel (T): fuzzy search, arrows to move, Space toggles.
  function tagPanel(target) {
    const ids = target.kind === "notes" ? target.ids : [];
    const label = target.kind === "notes" ? (ids.length > 1 ? `给 ${ids.length} 条想法打标签` : `给「${esc(M.title(byId(ids[0])))}」打标签`) : "按标签筛选";
    const node = dialog("nw-tag-panel", `${head(label, "输入搜索，↑↓ 选择，空格切换；回车新建。")}<input type="search" class="nw-tag-search" placeholder="搜索或新建标签" aria-label="搜索标签" aria-controls="nw-tag-options"><ul id="nw-tag-options" class="nw-tag-options" role="listbox" aria-multiselectable="true"></ul><p role="status" class="nw-form-status"></p>`, label);
    const input = node.querySelector("input"), list = node.querySelector("ul");
    let active = 0, busy = false, shown = [];
    const state = name => target.kind === "filter" ? view.tags.includes(name) : ids.every(id => byId(id).tags.includes(name));
    function paint() {
      const query = input.value.trim();
      shown = tags.filter(tag => M.fuzzy(tag.name, query)).map(tag => tag.name);
      if (query && !tags.some(tag => tag.name === query) && target.kind === "notes") shown.push({create: query});
      active = Math.min(active, Math.max(0, shown.length - 1));
      list.innerHTML = shown.map((item, index) => typeof item === "string"
        ? `<li role="option" id="nw-tag-opt-${index}" data-index="${index}" aria-selected="${state(item)}" class="${index === active ? "active" : ""}"><i class="nw-swatch tag-${esc(tagInfo(item).color)}" aria-hidden="true"></i><span>${esc(item)}</span>${state(item) ? '<b aria-hidden="true">✓</b>' : ""}</li>`
        : `<li role="option" id="nw-tag-opt-${index}" data-index="${index}" aria-selected="false" class="nw-tag-create ${index === active ? "active" : ""}">新建标签「${esc(item.create)}」</li>`).join("") || '<li class="nw-quiet">没有匹配的标签</li>';
      input.setAttribute("aria-activedescendant", shown.length ? `nw-tag-opt-${active}` : "");
      list.querySelector(".active")?.scrollIntoView({block: "nearest"});
    }
    async function toggle(index) {
      const item = shown[index];
      if (item === undefined || busy) return;
      if (target.kind === "filter") { toggleFilterTag(item); paint(); return; }
      const name = typeof item === "string" ? item : item.create, on = typeof item === "string" && state(name);
      busy = true; formStatus(node, "正在保存…");
      try {
        if (ids.length === 1) {
          const note = byId(ids[0]);
          await commit(() => write("update", {note_id: note.id, expected_version: note.version, tags: on ? note.tags.filter(tag => tag !== name) : [...note.tags, name]}));
        } else await commit(() => write("bulk", {note_ids: ids, expected_versions: versions(ids), op: on ? "tag_remove" : "tag_add", value: name}));
        formStatus(node, on ? `已移除「${name}」` : `已加上「${name}」`);
        if (typeof item !== "string") input.value = "";
      } catch (error) { formStatus(node, error.message); }
      busy = false; paint();
    }
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); active = (active + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % Math.max(1, shown.length); paint(); }
      else if ((event.key === " " || event.key === "Enter") && shown.length) { event.preventDefault(); void toggle(active); }
    });
    input.oninput = () => { active = 0; paint(); };
    list.addEventListener("click", event => { const option = event.target.closest("[data-index]"); if (option) { active = Number(option.dataset.index); void toggle(active); } });
    node.addEventListener("close", () => render());
    paint(); input.focus();
  }

  // Tag management: create, rename (merge on collision), recolour, pin, order, delete.
  function manageTags() {
    const node = dialog("nw-manage", `${head("管理标签", "改名会同步到所有想法；删除只把标签从想法上摘掉，想法本身不动。")}<form class="nw-tag-new"><input name="name" maxlength="20" placeholder="新标签名" aria-label="新标签名" required><button type="submit" class="btn primary">新建</button></form><p role="status" class="nw-form-status"></p><ul class="nw-manage-list"></ul>`, "管理标签");
    const list = node.querySelector("ul");
    async function run(label, work) {
      formStatus(node, "正在保存…");
      try { await commit(work); } catch (error) { formStatus(node, `${label}失败：${error.message}`); paint(); return false; }
      formStatus(node, `${label}完成。`); paint(); return true;
    }
    function paint() {
      const counts = M.counts(notes).tags;
      list.innerHTML = tags.length ? tags.map((tag, index) => `<li data-tag="${esc(tag.name)}"><details class="nw-color"><summary class="nw-swatch tag-${esc(tag.color)}" aria-label="颜色：${esc(tag.color)}，点按更改"></summary><div class="nw-palette" role="group" aria-label="选择颜色">${Array.from({length: 10}, (_, i) => `c${i}`).map(color => `<button type="button" class="nw-swatch tag-${color}" data-color="${color}" aria-label="颜色 ${color}" aria-pressed="${tag.color === color}"></button>`).join("")}</div></details><input class="nw-rename" value="${esc(tag.name)}" maxlength="20" aria-label="标签名「${esc(tag.name)}」，回车改名"><span class="nw-side-count">${counts.get(tag.name) || 0}</span><button type="button" class="icon-btn" data-tag-act="pin" aria-pressed="${tag.pinned}" title="${tag.pinned ? "取消置顶" : "置顶（可用 1–9 快速切换）"}" aria-label="${tag.pinned ? "取消置顶" : "置顶"}「${esc(tag.name)}」">▲</button><button type="button" class="icon-btn" data-tag-act="up" ${index === 0 ? "disabled" : ""} aria-label="上移「${esc(tag.name)}」">↑</button><button type="button" class="icon-btn" data-tag-act="down" ${index === tags.length - 1 ? "disabled" : ""} aria-label="下移「${esc(tag.name)}」">↓</button><button type="button" class="btn sm ghost danger-text" data-tag-act="remove">删除</button></li>`).join("") : '<li class="nw-quiet">还没有标签。</li>';
    }
    node.querySelector(".nw-tag-new").onsubmit = event => {
      event.preventDefault();
      const name = event.target.name.value.trim();
      if (name) void run("新建标签", () => tagWrite("create", {name})).then(ok => { if (ok) event.target.name.value = ""; });
    };
    list.addEventListener("click", async event => {
      const row = event.target.closest("[data-tag]"); if (!row) return;
      const tag = tagInfo(row.dataset.tag), color = event.target.closest("[data-color]"), act = event.target.closest("[data-tag-act]")?.dataset.tagAct;
      if (color) { await run("改色", () => tagWrite("recolor", {name: tag.name, color: color.dataset.color, expected_version: tag.version})); return; }
      if (act === "pin") await run(tag.pinned ? "取消置顶" : "置顶", () => tagWrite("pin", {name: tag.name, pinned: !tag.pinned, expected_version: tag.version}));
      else if (act === "up" || act === "down") {
        const names = tags.map(item => item.name), index = names.indexOf(tag.name), swap = index + (act === "up" ? -1 : 1);
        [names[index], names[swap]] = [names[swap], names[index]];
        await run("调整顺序", () => tagWrite("reorder", {names}));
        list.querySelector(`[data-tag="${CSS.escape(tag.name)}"] [data-tag-act="${act}"]`)?.focus();
      } else if (act === "remove") {
        const affected = M.counts(notes).tags.get(tag.name) || 0;
        if (await confirmDialog("删除标签", `删除「${esc(tag.name)}」？会从 ${affected} 条想法上摘掉这个标签，想法本身不动。`, "删除标签", true)) {
          if (await run("删除标签", () => tagWrite("remove", {name: tag.name, expected_version: tag.version})) && view.tags.includes(tag.name)) setView({tags: view.tags.filter(name => name !== tag.name)});
        }
      }
    });
    list.addEventListener("keydown", async event => {
      if (event.key !== "Enter" || event.isComposing || !event.target.matches(".nw-rename")) return;
      event.preventDefault();
      const tag = tagInfo(event.target.closest("[data-tag]").dataset.tag), next = event.target.value.trim();
      if (!next || next === tag.name) return;
      formStatus(node, "正在改名…");
      try { await tagWrite("rename", {name: tag.name, new_name: next, expected_version: tag.version}); }
      catch (error) {
        if (!(error.status === 409 && error.body.merge_required)) { formStatus(node, `改名失败：${error.message}`); if (error.status === 409) await reload(); paint(); return; }
        const into = error.body.target;
        if (!await confirmDialog("标签已存在", `「${esc(into)}」已经存在。合并 ${esc(tag.name)} → ${esc(into)}（影响 ${error.body.affected} 条）？`, "合并标签")) { formStatus(node, "没有改名，也没有合并。"); paint(); return; }
        if (await run("合并标签", () => tagWrite("merge", {from: [tag.name], into})) && view.tags.includes(tag.name)) setView({tags: [...new Set(view.tags.map(name => name === tag.name ? into : name))]});
        return;
      }
      await reload();
      if (view.tags.includes(tag.name)) setView({tags: view.tags.map(name => name === tag.name ? next : name)});
      formStatus(node, `已改名为「${next}」。`);
      paint();
    });
    node.addEventListener("close", () => render());
    paint(); node.querySelector("[name=name]").focus();
  }

  function bulkProjectDialog() {
    const ids = [...selected];
    const node = dialog("nw-bulk-project", `${head(`改 ${ids.length} 条想法的工作区`)}<form><label>工作区<select name="project">${projectOptions(null, true)}</select></label><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="submit" class="btn primary">确定</button></div></form>`, "改工作区");
    const form = node.querySelector("form");
    form.onsubmit = async event => {
      event.preventDefault(); lockForm(node, true);
      try { await commit(() => write("bulk", {note_ids: ids, expected_versions: versions(ids), op: "project", value: form.project.value || null})); node.close(); toast(`已改 ${ids.length} 条想法的工作区。`); }
      catch (error) { formStatus(node, error.message); lockForm(node, false); }
    };
  }

  // ---- ⋯ menu -----------------------------------------------------------------
  function closeMenu(restore = true) {
    if (!menu) return;
    const {node, button} = menu; menu = null; node.remove();
    button.setAttribute("aria-expanded", "false");
    if (restore && button.isConnected) button.focus();
  }
  function menuItems(note, snooze) {
    if (snooze) return [...SNOOZE_DAYS.map(days => [`snooze:${days}`, `暂缓 ${days} 天`]), ...(note.snoozed_until ? [["snooze:clear", "取消暂缓"]] : []), ["back", "← 返回"]];
    if (note.state !== "open") return [["original", "最初记录"], ["summaries", "讨论结论"]];
    return [["edit", "编辑"], ["pin", note.pinned_at ? "取消置顶" : "置顶"], ["tags", "打标签"], ["merge", "合并"], ["snooze", "暂缓…"],
      ...(note.stale ? [["promote", "转任务"]] : []), ["archive", "归档"], ["original", "最初记录"], ["summaries", "讨论结论"]];
  }
  function openMenu(button, note, snooze = false) {
    closeMenu(false);
    const node = document.createElement("div");
    node.className = "nw-menu"; node.setAttribute("role", "menu"); node.setAttribute("aria-label", `「${M.title(note)}」的更多操作`);
    node.innerHTML = menuItems(note, snooze).map(([act, label]) => `<button type="button" role="menuitem" data-menu="${act}">${label}</button>`).join("");
    document.body.append(node);
    const rect = button.getBoundingClientRect(), width = node.offsetWidth, height = node.offsetHeight;
    node.style.left = `${Math.max(8, Math.min(rect.right - width, innerWidth - width - 8))}px`;
    node.style.top = `${rect.bottom + 4 + height > innerHeight ? Math.max(8, rect.top - height - 4) : rect.bottom + 4}px`;
    menu = {node, button, note};
    button.setAttribute("aria-expanded", "true");
    node.querySelector("button").focus();
    node.addEventListener("keydown", event => {
      const items = [...node.querySelectorAll("button")], index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus(); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); }
      else if (event.key === "Tab") closeMenu(false);
    });
    node.addEventListener("click", event => {
      const item = event.target.closest("[data-menu]");
      if (item) void menuAction(item.dataset.menu, note, button);
    });
  }
  async function menuAction(act, note, button) {
    if (act === "snooze") { openMenu(button, note, true); return; }
    if (act === "back") { openMenu(button, note); return; }
    closeMenu(!["edit", "tags", "merge", "promote", "original", "summaries"].includes(act));
    if (act === "edit") editDialog(note);
    else if (act === "tags") tagPanel({kind: "notes", ids: [note.id]});
    else if (act === "merge") mergeDialog([note.id, ...(dupes.get(note.id) || [])]);
    else if (act === "promote") promoteDialog(note);
    else if (act === "original") originalDialog(note);
    else if (act === "summaries") summariesDialog(note);
    else if (act === "pin") await attempt(note.pinned_at ? "取消置顶" : "置顶", () => write("pin", {note_id: note.id, expected_version: note.version, pinned: !note.pinned_at}));
    else if (act === "archive") await archive(note);
    else if (act.startsWith("snooze:")) await snooze(note, act === "snooze:clear" ? null : Number(act.slice(7)));
  }
  document.addEventListener("pointerdown", event => { if (menu && !menu.node.contains(event.target) && event.target !== menu.button) closeMenu(false); });
  $("nw-main").addEventListener("scroll", () => closeMenu(false));
  window.addEventListener("resize", () => closeMenu(false));

  // ---- note actions -------------------------------------------------------------
  const archive = note => attempt("归档", () => write("archive", {note_id: note.id, expected_version: note.version}), () => toast("已归档。", {label: "撤销", run: () => { void attempt("恢复", () => write("restore", {note_id: note.id, expected_version: byId(note.id).version})); }}));
  const keep = note => attempt("保留", () => write("keep", {note_id: note.id, expected_version: note.version}), () => toast("已保留，重新开始计时。"));
  function snooze(note, days) {
    const until = days === null ? null : new Date(Date.now() + days * 86400000).toISOString();
    return attempt("暂缓", () => write("snooze", {note_id: note.id, expected_version: note.version, until}), () => toast(days === null ? "已取消暂缓。" : `已暂缓到 ${day(until)}，到期前不进入需要回顾。`));
  }

  // ---- review mode (R) ----------------------------------------------------------
  function startReview() {
    const visible = M.sorted(M.filter(notes, {...view, view: "open"}), view.sort);
    const stale = visible.filter(note => note.stale);
    const queue = (stale.length ? stale : visible).map(note => note.id);
    if (!queue.length) { toast("没有可回顾的想法。"); return; }
    const node = dialog("nw-review", "", "回顾想法");
    review = {node, queue, index: 0, done: new Set(), scope: stale.length ? "需要回顾" : "当前想法"};
    node.addEventListener("close", () => { review = null; render(); });
    node.addEventListener("keydown", reviewKey);
    node.addEventListener("click", event => { const key = event.target.closest("[data-key]")?.dataset.key; if (key) void reviewAct(key); });
    paintReview();
  }
  function paintReview() {
    const {node, queue, index, done, scope} = review, now = Date.now();
    const note = byId(queue[index]), finished = done.size === queue.length;
    const progress = `<div class="nw-review-progress"><span>${scope} · 第 ${Math.min(index + 1, queue.length)} / ${queue.length} 张 · 已处理 ${done.size}</span><div class="nw-progress" role="progressbar" aria-valuemin="0" aria-valuemax="${queue.length}" aria-valuenow="${done.size}" aria-label="回顾进度"><i style="width:${done.size / queue.length * 100}%"></i></div></div>`;
    if (finished) {
      node.innerHTML = `${head("回顾完了")}${progress}<div class="nw-empty"><h2>这一轮都处理了</h2><p>保留的想法重新开始计时；暂缓的到期前不会再出现。</p><button type="button" class="btn primary" data-close>回到想法墙</button></div>`;
      node.querySelector("[data-close]").focus(); return;
    }
    const keys = [["K", "保留"], ["A", "归档"], ["M", "合并"], ["H", "暂缓 7 天"], ["C", "去聊聊"], ["P", "转任务"]];
    const handled = done.has(note.id) || note.state !== "open";
    node.innerHTML = `${head("回顾想法", "一次处理一张，按键即可。Esc 退出。")}${progress}<div class="nw-review-stage"><button type="button" class="icon-btn nw-review-nav" data-key="ArrowLeft" aria-label="上一张" ${index === 0 ? "disabled" : ""}>←</button><div class="nw-review-card">${card(note, now)}${handled ? '<p class="nw-review-done">这张已处理。</p>' : ""}</div><button type="button" class="icon-btn nw-review-nav" data-key="ArrowRight" aria-label="下一张" ${index === queue.length - 1 ? "disabled" : ""}>→</button></div><div class="nw-review-keys" role="toolbar" aria-label="回顾操作">${keys.map(([key, label]) => `<button type="button" class="btn" data-key="${key}" aria-keyshortcuts="${key}" ${handled ? "disabled" : ""}><kbd>${key}</kbd>${label}</button>`).join("")}</div>`;
    for (const control of node.querySelectorAll(".nw-review-card .nw-acts, .nw-review-card .nw-check")) control.remove();
    // Keep focus inside the dialog so the single-key actions keep working.
    (node.querySelector(".nw-review-keys button:not([disabled])") || node.querySelector(".nw-review-card .nw-card")).focus({preventScroll: true});
  }
  function nextReview(id) {
    review.done.add(id);
    const after = review.queue.findIndex((item, index) => index > review.index && !review.done.has(item));
    const before = review.queue.findIndex(item => !review.done.has(item));
    review.index = after >= 0 ? after : before >= 0 ? before : review.index;
    paintReview();
  }
  function reviewKey(event) {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.target.closest("input,textarea,select")) return;
    const key = event.key.length === 1 ? event.key.toUpperCase() : event.key;
    if (["K", "A", "M", "H", "C", "P", "ArrowLeft", "ArrowRight"].includes(key)) { event.preventDefault(); void reviewAct(key); }
  }
  async function reviewAct(key) {
    if (!review || review.busy) return;
    const id = review.queue[review.index], note = byId(id);
    if (key === "ArrowLeft" || key === "ArrowRight") { review.index = Math.max(0, Math.min(review.queue.length - 1, review.index + (key === "ArrowLeft" ? -1 : 1))); paintReview(); return; }
    if (review.done.has(id) || note.state !== "open") return;
    if (key === "C") { location.href = chatUrl(note); return; }
    if (key === "M") { mergeDialog([id, ...(dupes.get(id) || [])], () => { if (review) nextReview(id); }); return; }
    if (key === "P") { promoteDialog(note, () => { if (review) nextReview(id); }); return; }
    review.busy = true;
    try {
      const work = {K: () => write("keep", {note_id: id, expected_version: note.version}), A: () => write("archive", {note_id: id, expected_version: note.version}),
        H: () => write("snooze", {note_id: id, expected_version: note.version, until: new Date(Date.now() + 7 * 86400000).toISOString()})}[key];
      await commit(work);
      review.busy = false; nextReview(id);
    } catch (error) { review.busy = false; paintReview(); toast(`${{K: "保留", A: "归档", H: "暂缓"}[key]}失败：${error.message}`); }
  }

  // ---- quick capture --------------------------------------------------------------
  const input = $("nw-input");
  try { input.value = localStorage.getItem(DRAFT_KEY) || ""; } catch {}
  function resizeInput() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 240)}px`; }
  function saveDraft() { try { localStorage.setItem(DRAFT_KEY, input.value); } catch {} }
  function closeSuggest() { suggest = null; $("nw-suggest").hidden = true; $("nw-suggest").innerHTML = ""; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
  function paintSuggest() {
    const list = $("nw-suggest");
    if (!suggest || !suggest.items.length) { closeSuggest(); return; }
    list.hidden = false; input.setAttribute("aria-expanded", "true");
    list.innerHTML = suggest.items.map((item, index) => `<li role="option" id="nw-suggest-${index}" data-index="${index}" aria-selected="${index === suggest.active}">${item.html}</li>`).join("");
    input.setAttribute("aria-activedescendant", `nw-suggest-${suggest.active}`);
  }
  function updateSuggest() {
    const found = M.token(input.value, input.selectionStart);
    if (!found) { closeSuggest(); return; }
    let items;
    if (found.kind === "#") {
      items = tags.filter(tag => M.fuzzy(tag.name, found.query)).slice(0, 8).map(tag => ({kind: "#", value: tag.name, html: `<i class="nw-swatch tag-${esc(tag.color)}" aria-hidden="true"></i>${esc(tag.name)}`}));
      if (found.query && !tags.some(tag => tag.name === found.query)) items.push({kind: "#", value: found.query, html: `新标签 #${esc(found.query)}`});
    } else {
      items = [{kind: "@", value: null, html: "全局（不属于工作区）"}, ...projects.map(item => ({kind: "@", value: item.name, html: esc(item.display_name || item.name)}))]
        .filter(item => M.fuzzy(item.value === null ? "全局" : projectName(item.value), found.query)).slice(0, 8);
    }
    suggest = {token: found, items, active: 0};
    paintSuggest();
  }
  function choose(index) {
    const item = suggest.items[index], {start, end} = suggest.token;
    const insert = item.kind === "#" ? `#${item.value} ` : "";
    input.value = input.value.slice(0, start) + insert + input.value.slice(end);
    input.selectionStart = input.selectionEnd = start + insert.length;
    if (item.kind === "@") { captureProject = item.value; renderCaptureExtra(); }
    closeSuggest(); saveDraft(); resizeInput(); input.focus();
  }
  input.addEventListener("input", () => { saveDraft(); resizeInput(); updateSuggest(); $("nw-capture-status").textContent = ""; });
  input.addEventListener("click", updateSuggest);
  input.addEventListener("blur", () => setTimeout(closeSuggest, 150));
  input.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if (suggest) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); suggest.active = (suggest.active + (event.key === "ArrowDown" ? 1 : -1) + suggest.items.length) % suggest.items.length; paintSuggest(); return; }
      if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); choose(suggest.active); return; }
      if (event.key === "Escape") { event.preventDefault(); closeSuggest(); return; }
    }
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("nw-capture").requestSubmit(); }
  });
  $("nw-suggest").addEventListener("mousedown", event => { const option = event.target.closest("[data-index]"); if (option) { event.preventDefault(); choose(Number(option.dataset.index)); } });
  $("nw-capture").addEventListener("submit", async event => {
    event.preventDefault();
    const parsed = M.parseCapture(input.value), status = $("nw-capture-status");
    if (!parsed.text) { status.textContent = input.value.trim() ? "只有标签还不够，写一句想法再记下。" : "先写点什么。"; return; }
    const data = {text: parsed.text, tags: [...new Set([...(view.untagged ? [] : view.tags), ...parsed.tags])], project: captureProject};
    const fingerprint = JSON.stringify(data);
    // An unconfirmed capture retries with the same request id, so it lands once.
    if (capturePending?.fingerprint !== fingerprint) capturePending = {fingerprint, id: window.CrewBrowser.requestId()};
    const button = $("nw-capture").querySelector("[type=submit]");
    button.disabled = true; status.textContent = "正在记下…";
    try { await api("/api/personal/notes", {action: "capture", data, request_id: capturePending.id}); }
    catch (error) { status.textContent = `没记下：${error.message}（内容还在输入框里）`; button.disabled = false; input.focus(); return; }
    capturePending = null; input.value = ""; saveDraft(); resizeInput(); captureProject = view.ws;
    button.disabled = false; input.focus();
    await reload();
    status.textContent = "已记下。不会因此启动 AI。";
  });

  // ---- events -----------------------------------------------------------------------
  function target() {
    if (selected.size) return {kind: "notes", ids: [...selected]};
    const focused = document.activeElement?.closest?.("#nw-wall .nw-card");
    return focused && byId(focused.dataset.id).state === "open" ? {kind: "notes", ids: [focused.dataset.id]} : {kind: "filter"};
  }
  async function pinnedTag(number) {
    const pinned = tags.filter(tag => tag.pinned), tag = pinned[number - 1];
    if (!tag) { toast(pinned.length ? `只有 ${pinned.length} 个置顶标签。` : "还没有置顶标签。在“管理标签”里置顶后，可以用 1–9 快速切换。"); return; }
    const goal = target();
    if (goal.kind === "filter") { toggleFilterTag(tag.name); return; }
    const on = goal.ids.every(id => byId(id).tags.includes(tag.name));
    const done = () => toast(on ? `已移除「${tag.name}」` : `已加上「${tag.name}」`);
    if (goal.ids.length === 1) { const note = byId(goal.ids[0]); await attempt("打标签", () => write("update", {note_id: note.id, expected_version: note.version, tags: on ? note.tags.filter(name => name !== tag.name) : [...note.tags, tag.name]}), done); }
    else await attempt("批量打标签", () => write("bulk", {note_ids: goal.ids, expected_versions: versions(goal.ids), op: on ? "tag_remove" : "tag_add", value: tag.name}), done);
  }
  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || menu || document.querySelector("dialog[open]")) return;
    if (event.target.closest("input,textarea,select,[contenteditable]")) return;
    const key = event.key;
    if (key === "r" || key === "R") { event.preventDefault(); startReview(); }
    else if (key === "t" || key === "T") { event.preventDefault(); tagPanel(target()); }
    else if (/^[1-9]$/.test(key)) { event.preventDefault(); void pinnedTag(Number(key)); }
    else if (key === "Escape" && (selected.size || selectMode)) { selected.clear(); selectMode = false; render(); }
  });

  document.addEventListener("click", async event => {
    const control = event.target.closest("[data-act]");
    if (!control || control.closest("dialog") || control.tagName === "SELECT") return;
    const act = control.dataset.act, cardNode = control.closest(".nw-card"), note = cardNode ? byId(cardNode.dataset.id) : null;
    if (act === "chat") return;
    if (act === "select") { if (control.checked) selected.add(note.id); else selected.delete(note.id); render(); return; }
    event.preventDefault();
    if (act === "menu") { if (menu?.button === control) closeMenu(); else openMenu(control, note); }
    else if (act === "filter-tag") toggleFilterTag(control.dataset.tag);
    else if (act === "untagged") setView({untagged: !view.untagged, tags: []});
    else if (act === "mode" || act === "group") setView({[act]: control.dataset.value});
    else if (act === "view") setView({view: control.dataset.value});
    else if (act === "view-open") setView({view: "open"});
    else if (act === "clear-filters") setView({tags: [], mode: "all", untagged: false, ws: null, global: false});
    else if (act === "tag-panel") tagPanel({kind: "filter"});
    else if (act === "manage-tags") manageTags();
    else if (act === "review") startReview();
    else if (act === "select-mode") { selectMode = !selectMode; if (!selectMode) selected.clear(); render(); }
    else if (act === "capture-global") { captureProject = null; renderCaptureExtra(); input.focus(); }
    else if (act === "expand") { if (expanded.has(note.id)) expanded.delete(note.id); else expanded.add(note.id); renderWall(); }
    else if (act === "promote") promoteDialog(note);
    else if (act === "recover") void recoverDialog(note);
    else if (act === "keep") await keep(note);
    else if (act === "archive") await archive(note);
    else if (act === "restore") await attempt("恢复", () => write("restore", {note_id: note.id, expected_version: note.version}), () => toast("已移回想法墙。"));
    else if (act === "unmerge") await attempt("撤销合并", () => write("unmerge", {note_id: note.id, expected_version: note.version}), () => toast("已撤销合并，这条想法回到墙上。"));
    else if (act === "summaries") summariesDialog(note);
    else if (act === "merge-dup") mergeDialog([note.id, ...dupes.get(note.id)]);
    else if (act === "bulk-tags") tagPanel({kind: "notes", ids: [...selected]});
    else if (act === "bulk-project") bulkProjectDialog();
    else if (act === "bulk-merge") mergeDialog([...selected]);
    else if (act === "bulk-archive") {
      const ids = [...selected];
      if (await confirmDialog("批量归档", `归档选中的 ${ids.length} 条想法？之后可以在“已归档”里恢复。`, "归档")) {
        await attempt("批量归档", () => write("bulk", {note_ids: ids, expected_versions: versions(ids), op: "archive", value: null}), () => { selected.clear(); render(); toast(`已归档 ${ids.length} 条想法。`); });
      }
    } else if (act === "bulk-clear") { selected.clear(); selectMode = false; render(); }
  });
  document.addEventListener("change", event => {
    const control = event.target.closest("#nw-filters [data-act]");
    if (!control) return;
    if (control.dataset.act === "scope") setView(control.value === "global" ? {ws: null, global: true} : control.value ? {ws: control.value.slice(3), global: false} : {ws: null, global: false});
    else if (control.dataset.act === "sort") setView({sort: control.value});
    else if (control.dataset.act === "view") setView({view: control.value});
  });
  // Coming back to the page re-reads: the assistant may have changed notes meanwhile.
  const visible = () => document.visibilityState === "visible" && window.crewTabVisible !== false;
  function onVisibility(event) {
    if (event.type === "crew-tab-visibility") window.crewTabVisible = event.detail.visible;
    if (visible() && loaded) void refresh().then(render, error => showError(error.message));
  }
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("crew-tab-visibility", onVisibility);

  resizeInput();
  (async () => { if (window.CrewAccess && !await CrewAccess.ready) return; render(); await boot(); })();
})();
