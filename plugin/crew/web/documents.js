"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const kinds = {summary:"汇总", report:"报告", guide:"指南", other:"其他"};
  const rank = item => (item.important ? 0 : 2) + (item.kind === "summary" ? 0 : 1);
  function sorted(items) {
    return items.slice().sort((a, b) => rank(a) - rank(b) || String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")) || String(a.id).localeCompare(String(b.id)));
  }
  function create(options) {
    const collections = new Map();
    const saving = new Set();
    const when = value => { const date = new Date(value); return Number.isNaN(date.valueOf()) ? "时间未记录" : date.toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}); };
    const byId = (scope, id) => collections.get(scope)?.items.find(item => String(item.id) === String(id));
    function notify() { options.onChange?.(); }
    function summary(item) { return item.summary ? `<p class="doc-summary">${esc(item.summary)}</p>` : '<p class="doc-summary missing">未填写摘要</p>'; }
    function preview(item) {
      const title = esc(item.title || item.path || "未命名文档");
      return item.exists === false ? `<span class="doc-title">${title}</span><span class="doc-missing">文件已不在原位置</span>` : `<a href="#" class="doc-title" data-file-path="${esc(item.path)}" data-document-id="${esc(item.id)}">${title}</a>`;
    }
    function card(item, scope) {
      const busy = saving.has(String(item.id));
      const identity = `data-doc-id="${esc(item.id)}" data-doc-scope="${esc(scope)}"`;
      return `<article class="document-card ${item.important ? "important" : ""}" data-document-card="${esc(item.id)}"><div class="document-top"><span class="doc-kind">${esc(kinds[item.kind] || kinds.other)}</span>${item.important ? '<span class="doc-important">重要</span>' : ""}<button class="icon-btn doc-pin" data-doc-act="pin" ${identity} aria-pressed="${Boolean(item.important)}" aria-label="${item.important ? "取消重要标记" : "标为重要"}" title="${item.important ? "取消重要标记" : "标为重要"}" ${busy ? "disabled" : ""}>${item.important ? "★" : "☆"}</button></div>${preview(item)}${summary(item)}<p class="doc-path" title="${esc(item.path)}">${esc(item.path)}</p><div class="doc-metadata"><span>${esc(item.group || "未分组")} · ${esc(item.submitter || "未记录提交者")}</span><time title="${esc(item.updated_at || item.created_at)}">更新于 ${esc(when(item.updated_at || item.created_at))}</time></div><div class="doc-actions">${item.exists === false ? "" : `<button class="btn ghost" data-doc-act="open" ${identity}>打开文件</button>`}<button class="btn ghost" data-doc-act="source" ${identity} ${item.leader_session_id || item.session_id ? "" : "disabled"}>回到来源对话</button>${typeof item.user_important === "boolean" ? `<button class="btn ghost doc-reset" data-doc-act="reset-pin" ${identity} ${busy ? "disabled" : ""}>恢复自动标记</button>` : ""}</div></article>`;
    }
    async function pin(item, value) {
      const id = String(item.id); if (saving.has(id)) return; saving.add(id); notify();
      try {
        const response = await options.api("/api/documents/pin", {project:options.project, id:item.id, important:value});
        const updated = response.document || response;
        if (String(updated.id) !== id) throw new Error("文档已经更新，请重新读取。");
        for (const collection of collections.values()) collection.items = collection.items.map(entry => String(entry.id) === id ? updated : entry);
      } catch (caught) { options.toast(caught.message); }
      finally { saving.delete(id); notify(); }
    }
    document.addEventListener("click", event => {
      const button = event.target.closest("[data-doc-act]"); if (!button || button.disabled) return;
      const act = button.dataset.docAct;
      const item = byId(button.dataset.docScope, button.dataset.docId);
      if (item && act === "pin") void pin(item, !item.important);
      else if (item && act === "reset-pin") void pin(item, null);
      else if (item && act === "source") options.onSource(item);
      else if (item && act === "open") void options.api("/api/files/open", {document_id:item.id}).catch(caught => options.toast(caught.message));
    });
    return {
      taskCards(list, goalId) {
        const scope = "goal:" + goalId, signature = JSON.stringify(list);
        if (collections.get(scope)?.signature !== signature) collections.set(scope, {signature,items:list});
        return sorted(collections.get(scope).items).map(item => card(item, scope)).join("");
      }
    };
  }
  window.CrewDocuments = {create, sorted};
})();
