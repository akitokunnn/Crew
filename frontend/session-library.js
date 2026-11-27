"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const categories = [["available","待接入"],["team","团队中"],["workspace_assistant","工作区助手"],["system_admin","系统管理员"],
    ["archived","历史归档"],["background","后台执行"],["task_history","任务历史"],["all","全部"]];
  const categoryName = item => item.category === "ungrouped" ? "未分组" : categories.find(([key]) => key === item.category)[1];
  const canAdopt = item => item.adoptable && (item.category === "available" || item.category === "ungrouped");
  const roomNames = item => item.war_rooms.map(room => room.title).join("、");
  const runtimeName = item => item.runtime === "codex" ? "Codex" : "CC";
  const status = item => item.running === true ? "正在运行" : item.running === null ? "运行状态未确认" : "未运行";
  function matches(item, filters) {
    if (filters.category !== "all" && !(filters.category === "available" ? ["available","ungrouped"].includes(item.category) : item.category === filters.category)) return false;
    if (filters.runtime !== "all" && item.runtime !== filters.runtime) return false;
    if (filters.status === "running" && item.running !== true) return false;
    if (filters.status === "ungrouped" && item.category !== "ungrouped") return false;
    const text = [item.title,item.name,item.group,item.project_name,item.cwd,item.native_id,roomNames(item)].join(" ").toLocaleLowerCase();
    return filters.query.toLocaleLowerCase().trim().split(/\s+/).every(term => text.includes(term));
  }
  function transcript(messages) {
    return messages.length ? messages.map(message => `<section class="sl-message"><div>${message.role === "user" ? "用户" : "AI"}</div><pre>${esc(message.text)}</pre></section>`).join("")
      : '<p class="note">暂无可预览的对话正文。</p>';
  }
  function executionMessages(events) {
    const messages = new Map();
    for (const event of events) {
      if (event.type === "message") messages.set(event.id, {role:"user", text:event.text});
      else if (event.type === "text") {
        const previous = messages.get(event.id)?.text ?? "";
        messages.set(event.id, {role:"assistant", text:previous + event.text});
      } else if (event.type === "assistant") {
        messages.set(event.id, {role:"assistant", text:event.content.filter(block => block.type === "text").map(block => block.text).join("\n")});
      }
    }
    return [...messages.values()].filter(message => message.text);
  }
  function create(options) {
    const root = document.createElement("section"); root.className = "session-library";
    let state, items = [], filters = {category:"available",runtime:"all",status:"all",query:""}, scope = "project";
    let selected = null, detail = null, error = "", warning = "", loading = false, busy = false, limit = 50;
    let taskLists = new Map(), taskRequest = 0, taskLoading = false, taskPreview = null;
    let request = 0, previewRequest = 0, loadedAt = 0, draft = {name:"",group:""}, dirty = false;
    root.innerHTML = `<header class="head big"><div><div class="title">会话库</div><div class="sub">找到已有工作，看清内容，再决定交给谁继续。</div></div><button class="btn" data-sl="refresh">刷新</button></header><div class="sl-body"><div class="sl-toolbar"><label class="sl-search"><span class="sl-search-label">搜索会话</span><input type="search" data-sl-field="query" placeholder="标题、名字、目录或会话 ID" autocomplete="off"></label><label>范围<select data-sl-field="scope"><option value="project">当前工作区</option><option value="all">全部工作区</option></select></label><label data-sl-session-filter>载体<select data-sl-field="runtime"><option value="all">全部载体</option><option value="claude">Claude Code</option><option value="codex">Codex</option></select></label><label data-sl-session-filter>状态<select data-sl-field="status"><option value="all">全部状态</option><option value="running">正在运行</option><option value="ungrouped">未分组</option></select></label></div><nav class="sl-categories" aria-label="会话分类"></nav><p class="sl-scope"></p><p class="sl-error" role="alert" hidden></p><p class="sl-warning" role="status" hidden></p><div class="sl-layout"><div class="sl-results" aria-live="polite"></div><aside class="sl-preview" aria-label="会话预览" hidden></aside></div></div>`;
    const find = selector => root.querySelector(selector);
    const when = value => new Date(value).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
    function notices() {
      for (const [selector,text] of [[".sl-error",error],[".sl-warning",warning]]) { const node = find(selector); node.hidden = !text; node.textContent = text; }
      find('[data-sl="refresh"]').disabled = loading || taskLoading || busy;
      const history = filters.category === "task_history";
      root.classList.toggle("sl-task-mode", history);
      find(".sl-search-label").textContent = history ? "搜索任务" : "搜索会话";
      find('[data-sl-field="query"]').placeholder = history ? "任务标题" : "标题、名字、目录或会话 ID";
      find(".sl-scope").textContent = `${scope === "project" ? state?.project?.display_name || state?.project?.name || "当前工作区" : "全部已登记工作区"} · 已发现 ${items.length} 个会话 · 最近活动优先。预览不会启动 AI。`;
    }
    function renderRow(item) {
      const rooms = roomNames(item);
      const location = item.category === "system_admin" ? "所有工作区"
        : `${scope === "all" ? item.project_name + " · " : ""}${item.rel_dir === "." ? "主目录" : item.rel_dir}`;
      const metadata = `${location}${item.last_active ? ` · ${when(item.last_active)}` : ""}${rooms ? ` · 作战室 · ${rooms}` : ""}`;
      const description = item.name && item.name !== item.title ? `<span class="sl-description"> · ${esc(item.title)}</span>` : "";
      return `<article class="sl-row ${selected === item.key ? "selected" : ""}"><button class="sl-row-main" data-sl="preview" data-key="${esc(item.key)}" aria-pressed="${selected === item.key}"><span class="sl-row-title">${esc(item.name || item.title)}${description}</span><span class="sl-row-tags"><span class="sl-carrier">${runtimeName(item)}</span><span>${categoryName(item)}</span><span class="${item.running === true && item.category !== "archived" ? "sl-running" : ""}">${status(item)}</span>${item.group ? `<span class="sl-row-group">归 ${esc(item.group)}</span>` : ""}</span>
        <span class="sl-location" title="${esc(metadata)}">${esc(metadata)}</span></button><button class="btn ghost sl-row-preview" data-sl="preview" data-key="${esc(item.key)}">预览</button></article>`;
    }
    function renderSessions(results, admins) {
      if (filters.category === "system_admin" && admins.length) return "";
      const resultCount = admins.length ? `工作区会话（${results.length}）` : `${results.length} 个符合条件`;
      const count = loading && !items.length ? "正在读取本机会话…" : resultCount;
      return `<div class="sl-result-heading"><span>${count}</span><button class="btn ghost" data-sl="clear">重置筛选</button></div>
        ${results.slice(0,limit).map(renderRow).join("")}${results.length > limit ? `<button class="btn sl-more" data-sl="more">再显示 ${Math.min(50,results.length-limit)} 个</button>` : ""}
        ${!results.length && !loading ? '<p class="sl-empty">没有符合筛选的会话。</p>' : ""}`;
    }
    function tasks() {
      return scope === "project" ? state.goals : [...taskLists.values()].flat();
    }
    function renderTasks() {
      if (taskLoading) return '<p class="note" role="status">正在读取任务…</p>';
      const query = filters.query.toLocaleLowerCase().trim();
      const results = tasks().filter(task => task.title.toLocaleLowerCase().includes(query));
      return results.length ? `<section class="sl-group" aria-label="任务历史"><h2>按任务查看执行记录</h2>${results.slice(0,limit).map(task =>
        `<button type="button" class="sl-task" data-sl="task" data-key="${esc(task.id)}" aria-pressed="${taskPreview?.id === task.id}"><strong>${esc(task.title)}</strong>${scope === "all" ? `<span>${esc(task.project)}</span>` : ""}</button>`).join("")}
        ${results.length > limit ? `<button class="btn sl-more" data-sl="more">再显示 ${Math.min(50,results.length-limit)} 个</button>` : ""}</section>`
        : '<p class="sl-empty">没有符合条件的任务。</p>';
    }
    async function loadTasks() {
      const revision = ++taskRequest;
      taskLoading = true; error = ""; renderList();
      try {
        const lists = await Promise.all(state.projects.map(async ({name}) => [name, (await options.readTask("/api/op/goal.list", name)).goals]));
        if (revision === taskRequest) taskLists = new Map(lists);
      } catch (caught) {
        if (revision === taskRequest) error = caught.message;
      } finally {
        if (revision === taskRequest) { taskLoading = false; renderList(); }
      }
    }
    function focusPreview() {
      const panel = find(".sl-preview");
      panel.tabIndex = -1; panel.focus({preventScroll:true}); panel.scrollIntoView({block:"nearest"});
    }
    async function showTask(task) {
      const revision = ++previewRequest;
      selected = null; detail = null; error = "";
      taskPreview = {id:task.id, title:task.title, project:task.project, runs:null, chosen:null, messages:null};
      renderList(); renderDetail(); focusPreview();
      try {
        const result = await options.readTask(`/api/op/goal.get?id=${encodeURIComponent(task.id)}`, task.project);
        if (revision !== previewRequest) return;
        taskPreview.runs = result.executions.slice().reverse();
        renderDetail();
      } catch (caught) {
        if (revision === previewRequest) { taskPreview.error = caught.message; renderDetail(); }
      }
    }
    async function showExecution(id) {
      const revision = ++previewRequest, task = taskPreview;
      const run = task.runs.find(item => item.id === id);
      task.chosen = id; task.messages = null; task.error = ""; renderDetail();
      try {
        const query = new URLSearchParams({goal_id:task.id,session_id:run.session_id});
        const history = await options.readTask(`/api/op/chat.history?${query}`, task.project);
        if (revision !== previewRequest) return;
        const events = history.execution_id === id ? history.events : history.previous_executions.find(item => item.execution_id === id)?.events;
        if (!events) throw new Error("这轮执行已不在任务记录中，请刷新后重试。");
        task.messages = executionMessages(events);
      } catch (caught) {
        if (revision === previewRequest) task.error = caught.message;
      }
      if (revision === previewRequest) { renderDetail(); focusPreview(); }
    }
    function renderTaskPreview() {
      const task = taskPreview;
      const people = new Map(items.filter(item => item.session_id).map(item => [item.session_id, item.name || item.title]));
      const runs = task.runs?.map(run => `<button class="btn ghost sl-execution" type="button" data-sl="execution" data-key="${esc(run.id)}" aria-pressed="${task.chosen === run.id}">${esc(people.get(run.session_id) ?? run.session_id)} · ${runtimeName(run)} · 第 ${run.round} 轮 / 第 ${run.generation} 次</button>`).join("");
      return `<div class="sl-preview-heading"><h2>${esc(task.title)}</h2><button class="btn ghost" data-sl="close">关闭</button></div>
        <p class="note">执行历史 · 只读</p>${task.error ? `<p class="error-line" role="alert">${esc(task.error)}</p>` : ""}
        ${runs || (task.runs ? '<p class="note">这个任务还没有执行记录。</p>' : task.error ? "" : '<p class="note">正在读取执行记录…</p>')}
        ${task.chosen && !task.error ? `<div class="sl-transcript">${task.messages === null ? '<p class="note">正在读取对话…</p>' : transcript(task.messages)}</div>` : ""}`;
    }
    function renderList() {
      const active = document.activeElement;
      const focus = root.contains(active) && active.dataset.sl ? {...active.dataset} : null;
      const nav = find(".sl-categories");
      nav.innerHTML = categories.map(([key,label]) => {
        const count = key === "task_history" ? "" : `<span>${items.filter(item => matches(item,{...filters,category:key,query:""})).length}</span>`;
        return `<button class="${filters.category === key ? "on" : ""}" data-sl="category" data-category="${key}" aria-pressed="${filters.category === key}">${label}${count}</button>`;
      }).join("");
      const history = filters.category === "task_history";
      const adminFilters = history ? {category:"system_admin",runtime:"all",status:"all",query:""} : {...filters,category:"system_admin"};
      const admins = items.filter(item => item.category === "system_admin" && matches(item,adminFilters));
      const results = items.filter(item => item.category !== "system_admin" && matches(item,filters));
      const adminSection = admins.length ? `<section class="sl-group" aria-label="系统管理员"><h2>系统管理员</h2>${admins.map(renderRow).join("")}</section>` : "";
      find(".sl-results").innerHTML = adminSection + (history ? renderTasks() : renderSessions(results, admins));
      notices();
      if (focus && !active.isConnected) {
        let selector = `[data-sl="${CSS.escape(focus.sl)}"]`;
        if (focus.key) selector += `[data-key="${CSS.escape(focus.key)}"]`;
        if (focus.category) selector += `[data-category="${CSS.escape(focus.category)}"]`;
        find(selector)?.focus({preventScroll:true});
      }
    }
    function leadersFor(item) { return item.project === state.project.name ? state.leaders : []; }
    function renderDetail() {
      const panel = find(".sl-preview"), open = selected !== null || taskPreview !== null;
      panel.hidden = !open; find(".sl-layout").classList.toggle("has-preview", open);
      if (taskPreview !== null) { panel.innerHTML = renderTaskPreview(); return; }
      if (selected === null) { panel.innerHTML = ""; return; }
      if (!detail) { panel.innerHTML = `<p class="note">正在读取最近对话…</p>`; return; }
      const item = detail.item, leaders = leadersFor(item);
      panel.innerHTML = `<div class="sl-preview-heading"><h2>${esc(item.name || item.title)}</h2><button class="btn ghost" data-sl="close" aria-label="关闭会话预览">关闭</button></div><div class="sl-row-tags"><span class="sl-carrier">${runtimeName(item)}</span><span>${categoryName(item)}</span><span>${status(item)}</span></div><p class="sl-preview-path">${esc(item.cwd)}</p><p class="sl-preview-id">会话 ${esc(item.native_id || item.session_id)}</p>${detail.error ? `<p class="error-line">${esc(detail.error)}</p>` : ""}<div class="sl-transcript">${transcript(detail.messages)}</div><p class="note">${detail.truncated ? "仅展示最近记录中的部分对话。" : ""}这里只读展示用户与 AI 的文字，不会恢复任务。</p>${canAdopt(item) && item.project === state.project.name ? `<form class="sl-adopt"><h3>接入团队</h3><p>保留原生会话，之后由所选 leader 接续工作。</p><label>叫它<input name="name" required maxlength="80" value="${esc(draft.name)}" ${busy ? "disabled" : ""}></label><label>交给哪位 leader<select name="group" required ${busy ? "disabled" : ""}><option value="">请选择 leader</option>${leaders.map(leader => `<option value="${esc(leader.name)}" ${draft.group === leader.name ? "selected" : ""}>${esc(leader.name)}</option>`).join("")}</select></label><button class="btn primary" type="submit" ${busy || !leaders.length ? "disabled" : ""}>${busy ? "正在接入…" : "确认接入"}</button>${!leaders.length ? '<p class="note">先创建一位 leader，再将会话交给它。</p>' : ""}</form>` : item.category === "system_admin" ? '<p class="note">系统管理员会话 · 只读预览</p>' : item.category === "workspace_assistant" ? '<p class="note">工作区助手会话 · 只读预览</p>' : item.project !== state.project.name ? '<p class="note">这是其他工作区的会话。请切换到对应工作区后操作。</p>' : item.category === "archived" ? '<p class="note">归档作为历史参考保留，不会自动回到团队或成为新 AI 的上下文。</p>' : item.category === "team" ? '<button class="btn primary" data-sl="open">进入对话</button>' : '<p class="note">后台执行和来源不明的记录仅供查阅。</p>'}`;
      if (busy) find('[data-sl="close"]').disabled = true;
    }
    async function load() {
      const revision = ++request, requestedScope = scope; loading = true; error = ""; notices();
      try {
        const result = await options.api(`/api/op/session_library.list?scope=${requestedScope}`);
        if (revision !== request) return;
        items = result.items; warning = result.warning || ""; loadedAt = Date.now();
        if (selected && !items.some(item => item.key === selected)) { selected = null; detail = null; ++previewRequest; renderDetail(); }
        if (detail && !dirty && !busy && !find(".sl-preview").contains(document.activeElement)) { detail.item = items.find(item => item.key === selected) || detail.item; renderDetail(); }
      } catch (caught) { if (revision === request) error = caught.message || String(caught); }
      finally { if (revision === request) { loading = false; renderList(); } }
    }
    async function preview(key) {
      const revision = ++previewRequest; selected = key; detail = null; taskPreview = null; dirty = false; error = ""; renderList(); renderDetail();
      try {
        const result = await options.api(`/api/op/session_library.preview?scope=${scope}&key=${encodeURIComponent(key)}`);
        if (revision !== previewRequest) return;
        detail = result; draft = {name:result.item.name || result.item.title.slice(0,20),group:""}; renderDetail();
        focusPreview();
      } catch (caught) { if (revision === previewRequest) { error = caught.message || String(caught); selected = null; renderDetail(); notices(); } }
    }
    root.addEventListener("input", event => {
      const field = event.target.dataset.slField;
      if (field === "query") { filters.query = event.target.value; limit = 50; renderList(); }
      if (event.target.name === "name") { draft.name = event.target.value; dirty = true; }
    });
    root.addEventListener("change", event => {
      const field = event.target.dataset.slField;
      if (field === "scope") {
        scope = event.target.value; selected = null; detail = null; taskPreview = null; items = []; ++previewRequest;
        ++taskRequest; taskLoading = false; taskLists.clear(); renderDetail(); void load();
        if (scope === "all" && filters.category === "task_history") void loadTasks();
      }
      else if (field) { filters[field] = event.target.value; limit = 50; renderList(); }
      if (event.target.name === "group") { draft.group = event.target.value; dirty = true; }
    });
    root.addEventListener("click", event => {
      const button = event.target.closest("button[data-sl]"); if (!button || button.disabled || busy) return;
      const action = button.dataset.sl;
      if (action === "refresh") {
        void load();
        if (scope === "all" && filters.category === "task_history") void loadTasks();
      }
      if (action === "open" && detail) options.open?.(detail.item);
      if (action === "category") {
        const next = button.dataset.category;
        if (next === "task_history" || filters.category === "task_history") {
          ++previewRequest; selected = null; detail = null; taskPreview = null; renderDetail();
        }
        filters.category = next; limit = 50; renderList();
        if (next === "task_history" && scope === "all") void loadTasks();
      }
      if (action === "preview") void preview(button.dataset.key);
      if (action === "task") void showTask(tasks().find(task => task.id === button.dataset.key));
      if (action === "execution") void showExecution(button.dataset.key);
      if (action === "close") { const key = taskPreview?.id ?? selected; ++previewRequest; selected = null; detail = null; taskPreview = null; renderDetail(); renderList(); root.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus(); }
      if (action === "more") { limit += 50; renderList(); }
      if (action === "clear") {
        filters = {category:"available",runtime:"all",status:"all",query:""}; limit = 50;
        for (const field of ["query","runtime","status"]) find(`[data-sl-field="${field}"]`).value = filters[field]; renderList();
      }
    });
    root.addEventListener("keydown", event => {
      if (event.key === "Escape" && (selected || taskPreview) && !busy) { event.preventDefault(); event.stopPropagation(); find('[data-sl="close"]')?.click(); }
    });
    root.addEventListener("submit", async event => {
      if (!event.target.matches(".sl-adopt")) return; event.preventDefault(); if (busy || !detail || !canAdopt(detail.item) || detail.item.project !== state.project.name) return;
      const key = selected, name = draft.name.trim(), group = draft.group;
      if (!name || !group) { error = "请填写名字并选择 leader。"; notices(); return; }
      busy = true; error = ""; renderDetail(); notices();
      try {
        await options.api('/api/op/session_library.adopt', {key,name,group});
        // A completed join is never reissued because a subsequent refresh fails.
        selected = null; detail = null; dirty = false; ++previewRequest;
        options.toast(`已接入 ${group}，通知已交给 leader`); renderDetail();
        try { await options.changed(); } catch (caught) { error = `已接入，团队列表刷新失败：${caught.message || caught}`; }
        await load();
      } catch (caught) { error = caught.message || String(caught); }
      finally { busy = false; renderDetail(); notices(); }
    });
    return {mount(target,nextState) {
      state = nextState;
      if (root.parentElement !== target) { target.replaceChildren(root); delete target.dataset.render; }
      if (!loading && !busy && Date.now() - loadedAt > 15000) void load();
      notices();
    }, refresh:load};
  }
  window.CrewSessionLibrary = {create,matches,categories,canAdopt,executionMessages};
})();
