"use strict";
// Global, local storage maintenance. All eligibility and byte counts come from
// the backend; this view never infers that a history or project file is trash.
(() => {
  const bytes = value => {
    const count = Math.max(0, Number(value) || 0);
    if (count < 1024) return `${count} B`;
    const unit = Math.min(3, Math.floor(Math.log(count) / Math.log(1024)));
    return `${(count / 1024 ** unit).toFixed(count / 1024 ** unit >= 100 ? 0 : 1)} ${["B", "KB", "MB", "GB"][unit]}`;
  };
  const when = value => { const date = new Date(value); return value && !Number.isNaN(date.getTime()) ? date.toLocaleString("zh-CN", {hour12:false}) : "未记录"; };
  const explain = value => typeof value === "string" ? value : value?.error ? `${value.path ? value.path + "：" : ""}${value.error}` : JSON.stringify(value);
  function node(tag, props = {}, children = []) {
    const item = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") item.textContent = value;
      else if (key === "class") item.className = value;
      else if (key === "style") Object.assign(item.style, value);
      else if (["disabled", "checked", "value", "type"].includes(key)) item[key] = value;
      else item.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of [].concat(children)) if (child !== null && child !== undefined && child !== false) item.append(typeof child === "string" ? document.createTextNode(child) : child);
    if (Object.hasOwn(props, "value")) item.value = props.value;
    return item;
  }
  const button = (action, label, props = {}) => node("button", {type:"button", class:"cs-button", "data-storage-action":action, "data-storage-focus":action, ...props}, label);
  const hint = text => node("p", {class:"st-hint", text});
  const metric = (label, value) => node("div", {class:"st-metric"}, [node("span", {text:label}), node("strong", {text:bytes(value)})]);

  function mount(host, options) {
    let active = true, snapshot = null, reading = false, busy = false, generation = 0, preview = null, confirmation = null;
    let policyDraft = null, error = "", note = "", outcome = null;
    const selected = new Set(), known = new Set();
    const root = node("div", {class:"crew-storage", "aria-label":"存储与清理"});
    const live = node("p", {class:"cs-status st-note", "data-storage-note":"", "aria-live":"polite", "aria-atomic":"true"});
    const content = node("div", {class:"st-content"});
    root.append(live, content);
    host.replaceChildren(root);
    const currentPolicy = () => ({automatic_enabled:Boolean(policyDraft.automatic_enabled), retention_days:Number(policyDraft.retention_days), keep_backups:Number(policyDraft.keep_backups), recycle_days:7});
    const dirty = () => policyDraft && snapshot && ["automatic_enabled", "retention_days", "keep_backups"].some(key => String(policyDraft[key]) !== String(snapshot.policy[key]));
    const candidates = () => snapshot.categories.filter(item => selected.has(item.id) && item.candidate_count > 0);
    const unavailable = () => reading || busy;
    function focus(key, reveal = false) { const item = [...root.querySelectorAll("[data-storage-focus]")].find(item => item.getAttribute("data-storage-focus") === key); if (item && !item.disabled) { item.focus({preventScroll:true}); if (reveal) item.scrollIntoView({block:"start"}); } }
    function status() {
      const target = root.querySelector("[data-storage-note]");
      if (target) { target.textContent = error || note; target.classList.toggle("cs-error", Boolean(error)); target.setAttribute("role", error ? "alert" : "status"); }
      const submit = root.querySelector('[data-storage-action="preview"]');
      if (submit) { submit.disabled = unavailable() || Boolean(preview) || candidates().length === 0; submit.textContent = busy ? "正在处理…" : "预览清理"; }
      const save = root.querySelector('[data-storage-action="save-policy"]');
      if (save) save.disabled = unavailable() || !dirty();
      const unsaved = root.querySelector("[data-storage-policy-dirty]");
      if (unsaved) unsaved.textContent = dirty() ? "有尚未保存的修改" : "回收站保留 7 天";
    }
    function category(item) {
      const eligible = item.candidate_count > 0, id = "storage-category-" + item.id;
      return node("li", {class:"st-category"}, [node("input", {id, type:"checkbox", checked:selected.has(item.id), disabled:unavailable() || Boolean(preview) || !eligible,
        "data-storage-category":item.id, "data-storage-focus":id, "aria-describedby":id + "-description"}),
      node("div", {class:"st-category-copy"}, [node("label", {for:id}, [node("strong", {text:item.label}), node("span", {class:"st-category-size", text:bytes(item.bytes)})]),
        node("p", {id:id + "-description", text:item.description}),
        node("p", {class:"st-category-meta", text:eligible ? `${item.file_count} 个文件 · 可立即释放 ${bytes(item.reclaimable_bytes)}${item.recyclable_bytes ? ` · 可移入回收站 ${bytes(item.recyclable_bytes)}` : ""}` : `${item.file_count} 个文件 · 当前没有可清理项`}),
        item.protected_reasons?.length ? node("details", {"data-storage-details":"reasons-" + item.id}, [node("summary", {text:"查看保留原因"}), node("ul", {}, item.protected_reasons.map(reason => node("li", {text:reason})))]) : null])]);
    }
    function previewSection() {
      const expires = Date.parse(preview.expires_at), expired = Number.isFinite(expires) && expires <= Date.now();
      return node("section", {class:"cs-card st-preview", "aria-labelledby":"storage-preview-title"}, [
        node("h2", {id:"storage-preview-title", tabindex:"-1", "data-storage-focus":"preview-title", text:"确认这次清理"}),
        node("div", {class:"st-preview-metrics"}, [metric("立即释放", preview.freed_bytes), metric("移入回收站 · 保留 7 天", preview.recycled_bytes)]),
        hint(`共 ${preview.file_count} 个文件。回收站仍占用磁盘，保留期结束后才释放空间。`),
        preview.warnings?.length ? node("ul", {class:"st-errors"}, preview.warnings.map(item => node("li", {text:explain(item)}))) : null,
        !preview.file_count ? hint("重新核对后没有符合条件的文件，请返回选择。") : null,
        preview.protected?.length ? node("details", {"data-storage-details":"protected"}, [node("summary", {text:"这些内容会保留"}), node("ul", {}, preview.protected.map(item => node("li", {text:explain(item)})))]) : null,
        preview.samples?.length ? node("details", {"data-storage-details":"samples"}, [node("summary", {text:`查看文件样本（${preview.samples.length} 项）`}),
          node("ul", {class:"st-file-list"}, preview.samples.map(item => node("li", {}, [node("span", {text:`${item.label} · ${bytes(item.bytes)} · ${item.action === "trash" || item.action === "recycle" ? "移入回收站" : "立即释放"}`}), node("code", {text:item.path})])))]) : null,
        expired ? node("p", {class:"cs-warning", text:"这份预览已过期，请返回重新预览。"}) : hint(`预览有效至 ${when(preview.expires_at)}，执行时会再次核对文件状态。`),
        node("div", {class:"st-actions"}, [button("cancel-preview", "返回选择", {disabled:busy}), button("confirm-clean", busy ? "正在清理…" : "确认清理", {class:"cs-button cs-primary", disabled:busy || expired || !preview.file_count})])]);
    }
    function historySection() {
      return node("section", {class:"cs-card"}, [node("div", {class:"cs-card-heading"}, [node("div", {}, [node("h2", {text:"回收站与清理记录"}), hint(`回收站当前占用 ${bytes(snapshot.trash_bytes)}。移入后保留 7 天，可在此恢复。`)]),
        button("empty-trash", "清空回收站", {disabled:unavailable() || !snapshot.trash_bytes})]),
      confirmation ? node("div", {class:"st-confirm", "aria-labelledby":"storage-empty-title"}, [node("h3", {id:"storage-empty-title", tabindex:"-1", "data-storage-focus":"empty-title", text:"清空回收站？"}),
        hint(`将永久删除回收站中的文件，预计释放 ${bytes(snapshot.trash_bytes)}。清空后无法恢复。`), node("div", {class:"st-actions"}, [button("cancel-empty", "保留回收站", {disabled:busy}), button("confirm-empty", busy ? "正在清空…" : "确认永久清空", {disabled:busy})])]) : null,
      snapshot.history.length ? node("ol", {class:"st-history"}, snapshot.history.map(item => {
        const restorable = item.restorable_count > 0;
        return node("li", {}, [node("div", {class:"st-history-heading"}, [node("div", {}, [node("strong", {text:when(item.created_at)}),
          node("p", {text:`${({completed:"已完成", partial:"部分完成", failed:"未完成", restored:"已恢复", emptied:"已清空"})[item.status] || item.status} · 释放 ${bytes(item.freed_bytes)} · 移入回收站 ${bytes(item.recycled_bytes)}`})]),
        restorable ? button("restore", "恢复文件", {"data-batch-id":item.batch_id, "data-storage-focus":"restore-" + item.batch_id, disabled:unavailable()}) : null]),
        restorable ? hint(`${item.restorable_count} 个文件可恢复 · 当前占用 ${bytes(item.trash_bytes)} · 保留至 ${when(item.expires_at)}`) : null,
        item.errors?.length ? node("details", {"data-storage-details":"history-" + item.batch_id}, [node("summary", {text:`查看 ${item.errors.length} 项未完成原因`}), node("ul", {class:"st-errors"}, item.errors.map(item => node("li", {text:explain(item)})))]) : null]);
      })) : hint("还没有清理记录。")]);
    }
    function policySection() {
      return node("section", {class:"cs-card"}, [node("h2", {text:"自动维护"}),
        node("label", {class:"st-toggle"}, [node("input", {type:"checkbox", checked:policyDraft.automatic_enabled, disabled:unavailable(), "data-storage-policy":"automatic_enabled", "data-storage-focus":"automatic-enabled"}), node("span", {text:"自动整理过期日志"})]),
        hint("按保留策略清理过期启动日志，并处理到期回收站。导出包、备份和未引用附件由你预览后手动回收。"),
        hint("后台启动一分钟后首次检查，之后每小时检查一次。关闭后，到期文件会继续保留，直到手动清理或重新开启。"),
        node("div", {class:"cs-fields st-policy-fields"}, [node("label", {}, ["保留天数", node("select", {value:policyDraft.retention_days, disabled:unavailable(), "data-storage-policy":"retention_days", "data-storage-focus":"retention-days", "aria-label":"保留天数"}, [7,30,90].map(days => node("option", {value:days, text:`${days} 天`})))]),
          node("label", {}, ["至少保留备份份数", node("select", {value:policyDraft.keep_backups, disabled:unavailable(), "data-storage-policy":"keep_backups", "data-storage-focus":"keep-backups", "aria-label":"至少保留备份份数"}, [3,5,10].map(count => node("option", {value:count, text:`${count} 份`})))] )]),
        node("div", {class:"st-actions"}, [button("save-policy", busy ? "正在处理…" : "保存维护策略", {disabled:unavailable() || !dirty()}), node("span", {class:"st-muted", "data-storage-policy-dirty":"", text:dirty() ? "有尚未保存的修改" : "回收站保留 7 天"})]),
        snapshot.last_maintenance ? hint(`最近自动维护：${when(snapshot.last_maintenance.at)}`) : null,
        snapshot.last_maintenance?.error ? node("p", {class:"st-errors", role:"alert", text:`自动维护未完成：${snapshot.last_maintenance.error}`}) : null,
        snapshot.last_maintenance?.errors?.length ? node("details", {"data-storage-details":"maintenance-errors"}, [node("summary", {text:`查看自动维护的 ${snapshot.last_maintenance.errors.length} 项未完成原因`}), node("ul", {class:"st-errors"}, snapshot.last_maintenance.errors.map(item => node("li", {text:explain(item)})))]) : null]);
    }
    function resultSection() {
      if (!outcome) return null;
      const value = outcome.value, errors = value.errors || [], failed = value.status === "failed", partial = value.status === "partial" || errors.length > 0;
      const title = outcome.action === "clean" ? failed ? "清理未完成" : partial ? "清理部分完成" : "清理完成" : outcome.action === "restore" ? failed || partial ? "恢复未全部完成" : "恢复完成" : failed || partial ? "回收站未全部清空" : "回收站已清空";
      return node("section", {class:"cs-card st-result", "aria-labelledby":"storage-result-title"}, [node("h2", {id:"storage-result-title", tabindex:"-1", "data-storage-focus":"result-title", text:title}),
        outcome.action === "clean" ? hint(`实际释放 ${bytes(value.freed_bytes)}，移入回收站 ${bytes(value.recycled_bytes)}，处理 ${value.file_count || 0} 个文件。`) : null,
        outcome.action === "restore" ? hint(`实际恢复 ${value.file_count || 0} 个文件。`) : null,
        outcome.action === "emptyTrash" ? hint(`实际释放 ${bytes(value.freed_bytes)}，删除 ${value.file_count || 0} 个文件。`) : null,
        errors.length ? node("ul", {class:"st-errors"}, errors.map(item => node("li", {text:explain(item)}))) : null]);
    }
    function render(focusKey) {
      if (!active) return;
      const focused = root.contains(document.activeElement) ? document.activeElement.getAttribute("data-storage-focus") : null;
      const expanded = new Set([...root.querySelectorAll("details[open]")].map(item => item.getAttribute("data-storage-details")));
      if (!snapshot) content.replaceChildren(node("section", {class:"cs-card"}, [node("h2", {text:reading ? "正在统计存储…" : "暂时无法读取存储"}),
        hint("统计本机 Crew 数据与缓存，不会启动 AI。"), button("refresh", reading ? "正在读取…" : "重新读取", {disabled:unavailable()})]));
      else {
        const total = Math.max(1, snapshot.total_bytes);
        const occupied = snapshot.categories.filter(item => item.bytes > 0);
        const actionable = snapshot.categories.filter(item => item.candidate_count > 0);
        const retained = snapshot.categories.filter(item => item.candidate_count === 0 && item.bytes > 0);
        const shade = index => Math.max(.22, .85 - index * .07);
        const replace = (...items) => content.replaceChildren(...items.filter(Boolean));
        replace(node("section", {class:"cs-card"}, [node("div", {class:"cs-card-heading"}, [node("div", {}, [node("h2", {text:"本机 Crew 存储"}), hint("查看存储占用，整理过期日志和可回收文件。项目文件与工作历史会保留。")]), button("refresh", reading ? "正在统计…" : "刷新", {disabled:unavailable()})]),
          node("div", {class:"st-metrics"}, [metric("总使用", snapshot.total_bytes), metric("可立即释放", snapshot.reclaimable_bytes), metric("回收站", snapshot.trash_bytes)]),
          node("div", {class:"st-usage", "aria-hidden":"true"}, occupied.map((item, index) => node("span", {style:{flexGrow:item.bytes / total, opacity:shade(index)}, title:`${item.label} ${bytes(item.bytes)}`}))),
          occupied.length ? node("div", {class:"st-legend", role:"list", "aria-label":"存储占用分布"}, occupied.map((item, index) => node("span", {role:"listitem"}, [
            node("i", {style:{opacity:shade(index)}, "aria-hidden":"true"}), node("span", {text:`${item.label} ${item.bytes / total < .01 ? "<1" : Math.round(item.bytes / total * 100)}%`})]))) : null,
          hint(`统计于 ${when(snapshot.scanned_at)} · 可移入回收站 ${bytes(snapshot.recyclable_bytes)}`),
          snapshot.warnings?.length ? node("ul", {class:"st-errors"}, snapshot.warnings.map(item => node("li", {text:explain(item)}))) : null,
          actionable.length ? node("ul", {class:"st-categories", "data-storage-actionable":""}, actionable.map(category)) : hint("当前没有符合保留策略的可清理项。"),
          node("div", {class:"st-actions"}, [button("preview", "预览清理", {class:"cs-button cs-primary", disabled:unavailable() || candidates().length === 0}), node("span", {class:"st-muted", "data-storage-selected-count":"", text:`已选择 ${candidates().length} 类`})]),
          retained.length ? node("details", {"data-storage-details":"retained"}, [node("summary", {text:`保留中的数据 · ${bytes(retained.reduce((sum, item) => sum + item.bytes, 0))}`}), node("ul", {class:"st-categories"}, retained.map(category))]) : null,
          snapshot.workspaces.length ? node("details", {"data-storage-details":"workspaces"}, [node("summary", {text:"查看工作区聊天与附件"}), hint("按已有记录归属统计，不含共享数据库和项目目录。"), node("ul", {class:"st-workspaces"}, snapshot.workspaces.map(item => node("li", {}, [node("span", {text:item.name}), node("span", {text:`${bytes(item.bytes)} · ${item.file_count} 个文件`})])))]) : null,
          node("details", {"data-storage-details":"home"}, [node("summary", {text:"数据位置"}), node("code", {class:"st-path", text:snapshot.home})])]),
        preview ? previewSection() : null, resultSection(), historySection(), policySection());
      }
      for (const detail of root.querySelectorAll("details")) if (expanded.has(detail.getAttribute("data-storage-details"))) detail.open = true;
      status();
      if (focusKey || focused) focus(focusKey || focused, Boolean(focusKey));
    }
    async function read() {
      if (!active || unavailable()) return;
      reading = true; const request = ++generation; error = ""; render();
      try {
        const value = await options.read();
        if (!active || request !== generation) return;
        snapshot = value;
        if (!policyDraft) policyDraft = {...value.policy};
        for (const item of value.categories) { if (!known.has(item.id) && item.default_selected && item.candidate_count > 0) selected.add(item.id); known.add(item.id); }
        preview = null; confirmation = null;
      } catch (failure) { if (active && request === generation) error = "读取存储失败：" + failure.message; }
      finally { if (active && request === generation) { reading = false; render(); } }
    }
    async function perform(action, payload) {
      if (!active || unavailable()) return;
      busy = true; const request = ++generation; error = ""; note = "正在处理…"; render();
      let nextFocus = null;
      try {
        const value = await options[action](payload);
        if (!active || request !== generation) return;
        note = "";
        if (action === "preview") { preview = value; outcome = null; nextFocus = "preview-title"; }
        else if (action === "savePolicy") {
          // Readback is authoritative; do not discard a draft on a failed save.
          snapshot.policy = value;
          policyDraft = {...snapshot.policy}; note = "维护策略已保存。";
        } else {
          outcome = {action, value}; preview = null; confirmation = null; nextFocus = "result-title";
          try { const latest = await options.read(); if (!active || request !== generation) return; snapshot = latest; }
          catch (failure) { if (active && request === generation) error = "操作已有结果，但刷新存储失败：" + failure.message; }
        }
      } catch (failure) { if (active && request === generation) { error = failure.message; note = ""; } }
      finally { if (active && request === generation) { busy = false; render(nextFocus); } }
    }
    function onInput(event) {
      if (unavailable()) return;
      const field = event.target.getAttribute("data-storage-policy");
      if (field) { policyDraft[field] = field === "automatic_enabled" ? event.target.checked : event.target.value; status(); }
      const category = event.target.getAttribute("data-storage-category");
      if (category) { event.target.checked ? selected.add(category) : selected.delete(category); root.querySelector("[data-storage-selected-count]").textContent = `已选择 ${candidates().length} 类`; status(); }
    }
    function onClick(event) {
      const target = event.target.closest("[data-storage-action]");
      if (!target || !root.contains(target) || target.disabled || unavailable()) return;
      const action = target.getAttribute("data-storage-action");
      if (action === "refresh") void read();
      else if (action === "preview") void perform("preview", {categories:candidates().map(item => item.id)});
      else if (action === "cancel-preview") { preview = null; error = ""; note = ""; render("preview"); }
      else if (action === "confirm-clean") {
        if (Date.parse(preview.expires_at) <= Date.now()) { error = "预览已过期，请返回重新预览。"; render(); return; }
        void perform("clean", {preview_id:preview.preview_id});
      } else if (action === "restore") void perform("restore", {batch_id:target.getAttribute("data-batch-id")});
      else if (action === "empty-trash") { confirmation = true; render("empty-title"); }
      else if (action === "cancel-empty") { confirmation = null; render("empty-trash"); }
      else if (action === "confirm-empty") void perform("emptyTrash", {});
      else if (action === "save-policy") {
        const policy = currentPolicy();
        if (![7,30,90].includes(policy.retention_days) || ![3,5,10].includes(policy.keep_backups)) { error = "请选择有效的保留天数和备份保留份数。"; status(); return; }
        void perform("savePolicy", policy);
      }
    }
    root.addEventListener("click", onClick); root.addEventListener("input", onInput); root.addEventListener("change", onInput);
    void read();
    return () => { active = false; generation++; root.removeEventListener("click", onClick); root.removeEventListener("input", onInput); root.removeEventListener("change", onInput); };
  }
  window.CrewStorage = {mount};
})();
