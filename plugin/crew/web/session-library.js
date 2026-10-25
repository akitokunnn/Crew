"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const categories = [["available","待接入"],["team","团队中"],["archived","历史归档"],["background","后台执行"],["all","全部"]];
  const categoryName = item => ({available:"待接入",ungrouped:"未分组",team:"团队中",archived:"已归档",background:"后台执行"})[item.category];
  const runtimeName = item => item.runtime === "codex" ? "Codex" : "CC";
  const status = item => item.running === true ? "正在运行" : item.running === null ? "运行状态未确认" : "未运行";
  function matches(item, filters) {
    if (filters.category !== "all" && !(filters.category === "available" ? ["available","ungrouped"].includes(item.category) : item.category === filters.category)) return false;
    if (filters.runtime !== "all" && item.runtime !== filters.runtime) return false;
    if (filters.status === "running" && item.running !== true) return false;
    if (filters.status === "ungrouped" && item.category !== "ungrouped") return false;
    const text = [item.title,item.name,item.group,item.project_name,item.cwd,item.native_id].join(" ").toLocaleLowerCase();
    return filters.query.toLocaleLowerCase().trim().split(/\s+/).every(term => text.includes(term));
  }
  function create(options) {
    const root = document.createElement("section"); root.className = "session-library";
    let state, items = [], filters = {category:"available",runtime:"all",status:"all",query:""}, scope = "project";
    let selected = null, detail = null, error = "", warning = "", loading = false, busy = false, limit = 50;
    let request = 0, previewRequest = 0, loadedAt = 0, draft = {name:"",group:""}, dirty = false;
    root.innerHTML = `<header class="head big"><div><div class="title">会话库</div><div class="sub">找到已有工作，看清内容，再决定交给谁继续。</div></div><button class="btn" data-sl="refresh">刷新</button></header><div class="sl-body"><div class="sl-toolbar"><label class="sl-search">搜索会话<input type="search" data-sl-field="query" placeholder="标题、名字、目录或会话 ID" autocomplete="off"></label><label>范围<select data-sl-field="scope"><option value="project">当前工作区</option><option value="all">全部已登记工作区</option></select></label><label>载体<select data-sl-field="runtime"><option value="all">全部载体</option><option value="claude">Claude Code</option><option value="codex">Codex</option></select></label><label>状态<select data-sl-field="status"><option value="all">全部状态</option><option value="running">正在运行</option><option value="ungrouped">未分组</option></select></label></div><nav class="sl-categories" aria-label="会话分类"></nav><p class="sl-scope"></p><p class="sl-error" role="alert" hidden></p><p class="sl-warning" role="status" hidden></p><div class="sl-layout"><div class="sl-results" aria-live="polite"></div><aside class="sl-preview" aria-label="会话预览" hidden></aside></div></div>`;
    const find = selector => root.querySelector(selector);
    const when = value => value ? new Date(value).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "时间未知";
    function notices() {
      for (const [selector,text] of [[".sl-error",error],[".sl-warning",warning]]) { const node = find(selector); node.hidden = !text; node.textContent = text; }
      find('[data-sl="refresh"]').disabled = loading || busy;
      find(".sl-scope").textContent = `${scope === "project" ? state?.project?.display_name || state?.project?.name || "当前工作区" : "全部已登记工作区"} · 已发现 ${items.length} 个会话 · 最近活动优先。预览不会启动 AI。`;
    }
    function renderList() {
      const active = document.activeElement;
      const focus = root.contains(active) && active.dataset.sl ? {...active.dataset} : null;
      const nav = find(".sl-categories");
      nav.innerHTML = categories.map(([key,label]) => {
        const count = items.filter(item => matches(item,{...filters,category:key,query:""})).length;
        return `<button class="${filters.category === key ? "on" : ""}" data-sl="category" data-category="${key}" aria-pressed="${filters.category === key}">${label}<span>${count}</span></button>`;
      }).join("");
      const results = items.filter(item => matches(item,filters));
      find(".sl-results").innerHTML = `<div class="sl-result-heading"><span>${loading && !items.length ? "正在读取本机会话…" : `${results.length} 个符合条件`}</span><button class="btn ghost" data-sl="clear">重置筛选</button></div>${results.slice(0,limit).map(item => `<article class="sl-row ${selected === item.key ? "selected" : ""}"><button class="sl-row-main" data-sl="preview" data-key="${esc(item.key)}" aria-pressed="${selected === item.key}"><span class="sl-row-title">${esc(item.name || item.title)}</span>${item.name && item.name !== item.title ? `<span class="sl-description">${esc(item.title)}</span>` : ""}<span class="sl-row-tags"><span class="sl-carrier">${runtimeName(item)}</span><span>${categoryName(item)}</span><span class="${item.running === true && item.category !== "archived" ? "sl-running" : ""}">${status(item)}</span>${item.group ? `<span>归 ${esc(item.group)}</span>` : ""}</span><span class="sl-location" title="${esc(item.cwd)}">${scope === "all" ? esc(item.project_name) + " · " : ""}${item.rel_dir === "." ? "主目录" : esc(item.rel_dir)}</span><time>${esc(when(item.last_active))}</time></button><button class="btn ghost sl-row-preview" data-sl="preview" data-key="${esc(item.key)}">预览</button></article>`).join("")}${results.length > limit ? `<button class="btn sl-more" data-sl="more">再显示 ${Math.min(50,results.length-limit)} 个</button>` : ""}${!results.length && !loading ? `<div class="sl-empty"><strong>${items.length ? "没有符合筛选的会话" : "这里还没有发现会话"}</strong><p>${items.length ? "可以换一个分类，或清除搜索与筛选。" : "本页汇集当前工作区的 CC、Codex 和 Crew 会话。新记录会在后台索引后出现，也可以切换范围。"}</p></div>` : ""}`;
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
      const panel = find(".sl-preview"); panel.hidden = selected === null;
      find(".sl-layout").classList.toggle("has-preview", selected !== null);
      if (selected === null) { panel.innerHTML = ""; return; }
      if (!detail) { panel.innerHTML = `<p class="note">正在读取最近对话…</p>`; return; }
      const item = detail.item, leaders = leadersFor(item);
      panel.innerHTML = `<div class="sl-preview-heading"><h2>${esc(item.name || item.title)}</h2><button class="btn ghost" data-sl="close" aria-label="关闭会话预览">关闭</button></div><div class="sl-row-tags"><span class="sl-carrier">${runtimeName(item)}</span><span>${categoryName(item)}</span><span>${status(item)}</span></div><p class="sl-preview-path">${esc(item.cwd)}</p><p class="sl-preview-id">会话 ${esc(item.native_id || item.session_id)}</p>${detail.error ? `<p class="error-line">${esc(detail.error)}</p>` : ""}<div class="sl-transcript">${detail.messages.length ? detail.messages.map(message => `<section class="sl-message"><div>${message.role === "user" ? "用户" : "AI"}</div><pre>${esc(message.text)}</pre></section>`).join("") : '<p class="note">暂无可预览的对话正文。仍可根据标题、目录和原会话 ID 辨认。</p>'}</div><p class="note">${detail.truncated ? "仅展示最近记录中的部分对话。" : ""}这里只读展示用户与 AI 的文字，不会恢复任务。</p>${item.adoptable && item.project === state.project.name ? `<form class="sl-adopt"><h3>接入团队</h3><p>保留原生会话，之后由所选 leader 接续工作。</p><label>叫它<input name="name" required maxlength="80" value="${esc(draft.name)}" ${busy ? "disabled" : ""}></label><label>交给哪位 leader<select name="group" required ${busy ? "disabled" : ""}><option value="">请选择 leader</option>${leaders.map(leader => `<option value="${esc(leader.name)}" ${draft.group === leader.name ? "selected" : ""}>${esc(leader.name)}</option>`).join("")}</select></label><button class="btn primary" type="submit" ${busy || !leaders.length ? "disabled" : ""}>${busy ? "正在接入…" : "确认接入"}</button>${!leaders.length ? '<p class="note">先创建一位 leader，再将会话交给它。</p>' : ""}</form>` : item.project !== state.project.name ? '<p class="note">这是其他工作区的会话。请切换到对应工作区后操作。</p>' : item.category === "archived" ? '<p class="note">归档作为历史参考保留，不会自动回到团队或成为新 AI 的上下文。</p>' : item.category === "team" ? '<button class="btn primary" data-sl="open">进入对话</button>' : '<p class="note">后台执行和来源不明的记录仅供查阅。</p>'}`;
      if (busy) find('[data-sl="close"]').disabled = true;
    }
    async function load() {
      const revision = ++request, requestedScope = scope; loading = true; error = ""; notices();
      try {
        const result = await options.api(`/api/session-library?scope=${requestedScope}`);
        if (revision !== request) return;
        items = result.items; warning = result.warning || ""; loadedAt = Date.now();
        if (selected && !items.some(item => item.key === selected)) { selected = null; detail = null; ++previewRequest; renderDetail(); }
        if (detail && !dirty && !busy && !find(".sl-preview").contains(document.activeElement)) { detail.item = items.find(item => item.key === selected) || detail.item; renderDetail(); }
      } catch (caught) { if (revision === request) error = caught.message || String(caught); }
      finally { if (revision === request) { loading = false; renderList(); } }
    }
    async function preview(key) {
      const revision = ++previewRequest; selected = key; detail = null; dirty = false; error = ""; renderList(); renderDetail();
      try {
        const result = await options.api(`/api/session-library/preview?scope=${scope}&key=${encodeURIComponent(key)}`);
        if (revision !== previewRequest) return;
        detail = result; draft = {name:result.item.name || result.item.title.slice(0,20),group:options.preferredLeader?.() || ""}; renderDetail();
        const panel = find(".sl-preview"); panel.tabIndex = -1; panel.focus({preventScroll:true}); panel.scrollIntoView({block:"nearest"});
      } catch (caught) { if (revision === previewRequest) { error = caught.message || String(caught); selected = null; renderDetail(); notices(); } }
    }
    root.addEventListener("input", event => {
      const field = event.target.dataset.slField;
      if (field === "query") { filters.query = event.target.value; limit = 50; renderList(); }
      if (event.target.name === "name") { draft.name = event.target.value; dirty = true; }
    });
    root.addEventListener("change", event => {
      const field = event.target.dataset.slField;
      if (field === "scope") { scope = event.target.value; selected = null; detail = null; items = []; ++previewRequest; renderDetail(); void load(); }
      else if (field) { filters[field] = event.target.value; limit = 50; renderList(); }
      if (event.target.name === "group") { draft.group = event.target.value; dirty = true; }
    });
    root.addEventListener("click", event => {
      const button = event.target.closest("button[data-sl]"); if (!button || button.disabled || busy) return;
      const action = button.dataset.sl;
      if (action === "refresh") void load();
      if (action === "open" && detail) options.open?.(detail.item);
      if (action === "category") { filters.category = button.dataset.category; limit = 50; renderList(); }
      if (action === "preview") void preview(button.dataset.key);
      if (action === "close") { const key = selected; ++previewRequest; selected = null; detail = null; renderDetail(); renderList(); root.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus(); }
      if (action === "more") { limit += 50; renderList(); }
      if (action === "clear") {
        filters = {category:"available",runtime:"all",status:"all",query:""}; limit = 50;
        for (const field of ["query","runtime","status"]) find(`[data-sl-field="${field}"]`).value = filters[field]; renderList();
      }
    });
    root.addEventListener("keydown", event => {
      if (event.key === "Escape" && selected && !busy) { event.preventDefault(); event.stopPropagation(); find('[data-sl="close"]')?.click(); }
    });
    root.addEventListener("submit", async event => {
      if (!event.target.matches(".sl-adopt")) return; event.preventDefault(); if (busy || !detail) return;
      const key = selected, name = draft.name.trim(), group = draft.group;
      if (!name || !group) { error = "请填写名字并选择 leader。"; notices(); return; }
      busy = true; error = ""; renderDetail(); notices();
      try {
        await options.api('/api/session-library/adopt', {key,name,group});
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
  window.CrewSessionLibrary = {create,matches,categories};
})();
