"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const kinds = {summary:"汇总", report:"报告", guide:"指南", other:"其他"};
  const groupKey = item => item.leader_session_id || item.group || "ungrouped";
  const rank = item => (item.important ? 0 : 2) + (item.kind === "summary" ? 0 : 1);
  function sorted(items) {
    return items.slice().sort((a, b) => rank(a) - rank(b) || String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")) || String(a.id).localeCompare(String(b.id)));
  }
  function filtered(items, filters) {
    const query = (filters.query || "").trim().toLocaleLowerCase();
    return sorted(items.filter(item => (!filters.important || item.important) && (!filters.kind || item.kind === filters.kind) &&
      (!filters.group || groupKey(item) === filters.group) && (!query || [item.title, item.summary, item.path, item.submitter, item.group].some(value => String(value || "").toLocaleLowerCase().includes(query)))));
  }
  function create(options) {
    let items = [], loaded = false, loading = false, lastLoad = 0, inflight = null, error = "", scan = null;
    let centerHost = null, railHost = null, activeLeader = null, railActive = false, drawerOpen = false;
    const filters = {query:"", group:"", kind:"", important:false}, saving = new Set();
    const icon = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2H5v20h14V8l-6-6Zm0 0v6h6"/></svg>';
    const when = value => { const date = new Date(value); return Number.isNaN(date.valueOf()) ? "时间未记录" : date.toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}); };
    const byId = id => items.find(item => String(item.id) === String(id));
    const groupItems = () => sorted(items.filter(item => item.leader_session_id === activeLeader || item.session_id === activeLeader));
    function notify() { renderResults(); renderRail(); options.onChange?.(); }
    function summary(item) { return item.summary ? `<p class="doc-summary">${esc(item.summary)}</p>` : '<p class="doc-summary missing">未填写摘要</p>'; }
    function preview(item, small = false) {
      const title = esc(item.title || item.path || "未命名文档");
      return item.exists === false ? `<span class="${small ? "doc-rail-title" : "doc-title"}">${title}</span><span class="doc-missing">文件已不在原位置</span>` : `<a href="#" class="${small ? "doc-rail-title" : "doc-title"}" data-file-path="${esc(item.path)}" data-document-id="${esc(item.id)}">${title}</a>`;
    }
    function card(item, small = false) {
      const busy = saving.has(String(item.id));
      if (small) return `<article class="doc-rail-card" data-document-card="${esc(item.id)}"><div class="doc-rail-meta"><span>${item.important ? "重要 · " : ""}${esc(kinds[item.kind] || kinds.other)}</span><span>${esc(item.submitter || "未记录提交者")}</span></div>${preview(item, true)}${summary(item)}</article>`;
      return `<article class="document-card ${item.important ? "important" : ""}" data-document-card="${esc(item.id)}"><div class="document-top"><span class="doc-kind">${esc(kinds[item.kind] || kinds.other)}</span>${item.important ? '<span class="doc-important">重要</span>' : ""}<button class="icon-btn doc-pin" data-doc-act="pin" data-doc-id="${esc(item.id)}" aria-pressed="${Boolean(item.important)}" aria-label="${item.important ? "取消重要标记" : "标为重要"}" title="${item.important ? "取消重要标记" : "标为重要"}" ${busy ? "disabled" : ""}>${item.important ? "★" : "☆"}</button></div>${preview(item)}${summary(item)}<p class="doc-path" title="${esc(item.path)}">${esc(item.path)}</p><div class="doc-metadata"><span>${esc(item.group || "未分组")} · ${esc(item.submitter || "未记录提交者")}</span><time title="${esc(item.updated_at || item.created_at)}">更新于 ${esc(when(item.updated_at || item.created_at))}</time></div><div class="doc-actions">${item.exists === false ? "" : `<button class="btn ghost" data-doc-act="open" data-doc-id="${esc(item.id)}">打开文件</button>`}<button class="btn ghost" data-doc-act="source" data-doc-id="${esc(item.id)}" ${item.leader_session_id || item.session_id ? "" : "disabled"}>回到来源对话</button>${typeof item.user_important === "boolean" ? `<button class="btn ghost doc-reset" data-doc-act="reset-pin" data-doc-id="${esc(item.id)}" ${busy ? "disabled" : ""}>恢复自动标记</button>` : ""}</div></article>`;
    }
    function groups() {
      const result = new Map(); for (const item of items) result.set(groupKey(item), item.group || "未分组");
      return [...result].sort((a, b) => a[1].localeCompare(b[1], "zh-CN"));
    }
    function renderResults() {
      const root = centerHost?.querySelector("#documents-center"); if (!root) return;
      const list = filtered(items, filters);
      const markup = list.map(item => card(item)).join("") || `<div class="documents-empty">${loading && !loaded ? "正在读取文档…" : error && !loaded ? "文档暂时无法读取，可以重试。" : items.length ? "没有符合条件的文档。" : "还没有文档。会话提交的汇总、报告和指南会收在这里。"}</div>`;
      const results = root.querySelector("#documents-results"); if (results.innerHTML !== markup) results.innerHTML = markup;
      root.querySelector("#documents-result-count").textContent = `${list.length} 份文档`;
      root.querySelector("#documents-error").textContent = error;
      root.querySelector("#documents-error").hidden = !error;
      root.querySelector("#documents-refresh").disabled = loading;
      root.querySelector("#documents-refresh").textContent = loading ? "正在整理…" : "重新整理";
      const more = root.querySelector("#documents-more"); more.hidden = !scan?.has_more; more.disabled = loading;
      root.querySelector("#documents-scan-note").textContent = scan?.has_more ? "部分历史文档还在索引中，可以继续读取。" : "重要汇总优先，按最近更新排列。";
      const group = root.querySelector("#documents-group"), selected = filters.group;
      const choices = '<option value="">全部分组</option>' + groups().map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join("");
      if (group.innerHTML !== choices) group.innerHTML = choices;
      group.value = selected;
    }
    function mountCenter(host, group) {
      centerHost = host;
      if (group !== undefined) filters.group = group || "";
      const fresh = !host.querySelector("#documents-center");
      if (fresh) {
        host.innerHTML = `<section id="documents-center" class="documents-center"><div class="head"><div><div class="title">文档中心</div><div class="sub" id="documents-result-count"></div></div><button class="btn" id="documents-refresh" data-doc-act="refresh">重新整理</button></div><div class="documents-scroll"><div class="documents-in"><div class="documents-filters"><label class="doc-search"><span class="sr-only">搜索文档</span><input id="documents-search" type="search" placeholder="搜索标题、摘要或路径" value="${esc(filters.query)}"></label><label><span class="sr-only">分组</span><select id="documents-group"></select></label><label><span class="sr-only">文档类型</span><select id="documents-kind"><option value="">全部类型</option>${Object.entries(kinds).map(([id, name]) => `<option value="${id}">${name}</option>`).join("")}</select></label><label class="doc-important-filter"><input id="documents-important" type="checkbox" ${filters.important ? "checked" : ""}>只看重要</label></div><p class="error-line" id="documents-error" role="alert" hidden></p><p class="documents-note" id="documents-scan-note"></p><div class="documents-results" id="documents-results"></div><button id="documents-more" class="btn" data-doc-act="more" hidden>继续读取历史文档</button></div></div></section>`;
        host.querySelector("#documents-kind").value = filters.kind;
      }
      renderResults(); void refresh(fresh);
    }
    function updateDrawer() {
      if (!railHost) return;
      railHost.classList.toggle("open", drawerOpen && railActive);
      const scrim = document.getElementById("documents-scrim"); if (scrim) scrim.hidden = !drawerOpen || !railActive || innerWidth >= 1360;
      const button = document.querySelector('[data-act="documents-open"]');
      if (button) { const open = railActive && (innerWidth >= 1360 || drawerOpen); button.setAttribute("aria-expanded", String(open)); button.classList.toggle("active", open); }
    }
    function renderRail(host, leaderId, active) {
      const changed = host && leaderId !== activeLeader;
      if (host) { railHost = host; activeLeader = leaderId; railActive = active; }
      if (!railHost) return;
      railHost.hidden = !railActive;
      const list = groupItems();
      const markup = `<div class="doc-rail-head"><h2 tabindex="-1">本组文档 <span>${list.length}</span></h2><button class="icon-btn" data-doc-act="more" aria-label="刷新本组文档" title="刷新本组文档">↻</button><button class="icon-btn doc-drawer-close" data-doc-act="close-rail" aria-label="关闭本组文档">×</button></div><div class="doc-rail-body">${error ? `<p class="error-line">${esc(error)}</p><button class="btn ghost" data-doc-act="more">重试</button>` : ""}${list.slice(0, 5).map(item => card(item, true)).join("") || `<p class="doc-rail-empty">${loading && !loaded ? "正在读取…" : "本组还没有文档"}</p>`}</div><button class="btn ghost doc-all" data-doc-act="center">查看本组全部 ${list.length ? `· ${list.length}` : ""}</button>`;
      if (railHost.innerHTML !== markup) railHost.innerHTML = markup;
      updateDrawer();
      if (railActive) void refresh(Boolean(changed));
    }
    async function refresh(force = false, rebuild = false) {
      if (inflight) return inflight;
      if (saving.size) return;
      if (!force && lastLoad && Date.now() - lastLoad < 30000) return;
      loading = true; error = "";
      inflight = (async () => {
        try {
          const result = rebuild ? await options.api("/api/documents/refresh", {project:options.project}) : await options.api("/api/documents");
          if (!Array.isArray(result.documents)) throw new Error("文档列表格式无效");
          items = result.documents; scan = result.scan; loaded = true; lastLoad = Date.now();
        } catch (caught) { error = caught.message || String(caught); lastLoad = Date.now(); }
        finally { loading = false; inflight = null; notify(); }
      })();
      renderResults(); return inflight;
    }
    async function pin(item, value) {
      const id = String(item.id); if (saving.has(id)) return; saving.add(id); notify();
      try {
        if (inflight) await inflight;
        const response = await options.api("/api/documents/pin", {project:options.project, id:item.id, important:value});
        const updated = response.document || response;
        if (String(updated.id) !== id) throw new Error("文档已经更新，请重新读取。");
        items = items.map(entry => String(entry.id) === id ? updated : entry);
      } catch (caught) { options.toast(caught.message); }
      finally { saving.delete(id); notify(); }
    }
    document.addEventListener("click", event => {
      const button = event.target.closest("[data-doc-act]"); if (!button || button.disabled) return;
      const act = button.dataset.docAct, item = byId(button.dataset.docId);
      if (act === "refresh") void refresh(true, true);
      else if (act === "more") void refresh(true);
      else if (act === "close-rail") closeRail();
      else if (act === "center") { filters.group = activeLeader || ""; closeRail(); options.onCenter(); }
      else if (item && act === "pin") void pin(item, !item.important);
      else if (item && act === "reset-pin") void pin(item, null);
      else if (item && act === "source") { closeRail(); options.onSource(item); }
      else if (item && act === "open") void options.api("/api/files/open", {document_id:item.id}).catch(caught => options.toast(caught.message));
    });
    document.addEventListener("input", event => { if (event.target.id === "documents-search") { filters.query = event.target.value; renderResults(); } });
    document.addEventListener("change", event => {
      const fields = {"documents-group":"group", "documents-kind":"kind", "documents-important":"important"}, field = fields[event.target.id];
      if (field) { filters[field] = field === "important" ? event.target.checked : event.target.value; renderResults(); }
    });
    document.addEventListener("keydown", event => { if (event.key === "Escape") closeRail(); });
    document.getElementById("documents-scrim")?.addEventListener("click", closeRail);
    window.addEventListener("resize", updateDrawer);
    function closeRail() { drawerOpen = false; updateDrawer(); }
    return {count:() => loaded ? items.length : null, groupCount:id => items.filter(item => item.leader_session_id === id || item.session_id === id).length,
      mountCenter, renderRail, refresh, closeRail, openRail() { drawerOpen = true; updateDrawer(); railHost?.querySelector("h2")?.focus(); }};
  }
  window.CrewDocuments = {create, sorted, filtered};
})();
