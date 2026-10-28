"use strict";
// Durable task artifacts are read from their own authenticated endpoints.
(() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const id = () =>
    globalThis.crypto?.randomUUID?.() ||
    `artifact-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const memory = new Map();
  const bytes = (n) =>
    n >= 1048576
      ? `${(n / 1048576).toFixed(1)} MB`
      : `${Math.ceil(n / 1024)} KB`;
  function create({
    host,
    client,
    project,
    taskId,
    sessionId,
    taskTitle = "",
    documents = [],
    reports = [],
    root = "",
    expanded = false,
    onExpand = () => {},
    toast = () => {},
    onChanged = () => {},
  }) {
    const key = `${project}:${taskId}`;
    const cache = memory.get(key) || {
      report: null,
      pendingReport: null,
      attachments: [],
      versions: [],
      draft: null,
      scroll: 0,
      expanded: new Set(),
      editing: false,
    };
    memory.set(key, cache);
    let disposed = false,
      generation = 0,
      loading = false,
      saving = false,
      errors = [],
      preview = null,
      localFolderAvailable = false,
      folderAccessError = "";
    const renderer = window.CrewDOMRenderer.create();
    const path = (url, values = {}) => {
      const [base, query = ""] = url.split("?");
      const params = new URLSearchParams(query);
      params.set("project", project);
      params.set("task_id", taskId);
      for (const [name, value] of Object.entries(values))
        params.set(name, value);
      return base + "?" + params;
    };
    const api = (url, body) => client.request(path(url), body);
    const url = (endpoint, values) =>
      client.resourceURL(path(endpoint, values));
    const draftKey = `crew.report-draft:${key}`;
    try {
      if (!cache.draft)
        cache.draft = JSON.parse(localStorage.getItem(draftKey));
    } catch (_) {
      /* No saved edit. */
    }
    function storeDraft() {
      if (cache.draft)
        try {
          localStorage.setItem(draftKey, JSON.stringify(cache.draft));
        } catch (error) {
          toast(error.message);
        }
    }
    function savePosition() {
      cache.scroll = host.closest(".tw-drawer-body")?.scrollTop || 0;
    }
    function attachment(item) {
      const link = url("/api/tasks/attachment", { attachment_id: item.id });
      const webpage =
        item.mime_type === "text/html" || /\.html?$/i.test(item.filename);
      return `<div class="td-attachment">
        <div class="td-file-heading"><strong>${esc(item.filename)}</strong><a href="${esc(link)}" target="_blank" rel="noopener">${webpage ? "预览网页" : "打开文件"} ↗</a></div>
        <small>${esc(item.mime_type)} · ${bytes(item.size_bytes)}</small>
        ${item.mime_type?.startsWith("image/") ? `<a href="${esc(link)}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(link)}" alt="${esc(item.filename)}"></a>` : ""}
        <a class="td-download" href="${esc(link)}" download="${esc(item.filename)}">下载文件</a>
      </div>`;
    }
    function latestMarkup() {
      const version = cache.versions[0];
      if (!version)
        return loading
          ? '<p class="td-empty" role="status">正在读取阶段成果…</p>'
          : '<p class="td-empty">阶段成果生成后，会出现在这里。</p>';
      const files = version.files.filter((file) => file.delivered_sha256);
      const entry =
        files.find((file) => /(^|\/)index\.html?$/i.test(file.path)) ||
        files.find((file) => /\.html?$/i.test(file.path)) ||
        files[0];
      const webpage = entry && /\.html?$/i.test(entry.path);
      return `<section class="td-latest">
        <span class="td-eyebrow">最新阶段成果</span>
        <h3>${esc(version.label || `成果版本 ${version.version}`)}</h3>
        <p>阶段版本 ${esc(version.version)} · ${version.files.length} 个文件${entry ? ` · ${esc(entry.path)}` : " · 此版本记录了文件删除"}</p>
        <div class="td-latest-actions">
          ${entry ? `<a class="td-primary-link" href="${esc(url("/api/tasks/version-file", { version_id: version.id, path: entry.path, side: "delivered" }))}" target="_blank" rel="noopener">${webpage ? "预览网页" : "查看成果文件"} ↗</a>` : ""}
          <button data-deliverable-action="show-versions">版本与恢复</button>
        </div>
      </section>`;
    }
    function folderMarkup() {
      return root ? `<div class="td-folder">
        <div class="td-folder-actions"><span>工作区文件夹</span><div>${localFolderAvailable ? '<button data-deliverable-action="open-folder">打开文件夹 ↗</button>' : ""}<button data-deliverable-action="copy-folder">复制路径</button></div></div>
        <details data-td-fold="folder"><summary>查看路径</summary><code>${esc(root)}</code></details>
        ${folderAccessError ? `<p role="status">${esc(folderAccessError)}</p>` : ""}
      </div>` : "";
    }
    function reportMarkup() {
      const report = cache.report;
      const latest = cache.pendingReport || report;
      if (cache.editing)
        return `<form class="td-report-form"><p>报告稿次 ${cache.draft.version} · 原文持续保留</p>${latest && latest.version !== cache.draft.version ? `<div class="td-update"><p>服务器已有稿次 ${latest.version}，你的草稿没有被覆盖。</p><details><summary>核对服务器当前内容</summary><article class="markdown">${window.CrewChat.renderMarkdown(latest.markdown, sessionId)}</article></details><button type="button" data-deliverable-action="rebase-report">已核对，以当前稿次提交这份草稿</button></div>` : ""}<textarea data-preserve="report-edit" aria-label="编辑本任务报告" rows="18">${esc(cache.draft.markdown)}</textarea><div class="td-actions"><button class="btn primary" type="submit" ${saving ? "disabled" : ""}>保存报告</button><button class="btn" type="button" data-deliverable-action="cancel-edit">返回阅读</button></div><p class="td-save-error" role="alert"></p></form>`;
      const source = report?.markdown || "";
      const updated = cache.pendingReport
        ? `<div class="td-update" role="status">报告有新稿次，当前内容和阅读位置保留。<button data-deliverable-action="apply-report">查看稿次 ${cache.pendingReport.version}</button></div>`
        : "";
      return `${updated}<div class="td-report-title"><span>持续报告 · 稿次 ${report?.version || 0}</span><button data-deliverable-action="edit-report">编辑</button></div>${source ? `<article class="markdown td-report">${window.CrewChat.renderMarkdown(source, sessionId)}</article>` : '<p class="td-empty">还没有持续报告。新的结论和阶段成果会留在本任务。</p>'}`;
    }
    function versionsMarkup() {
      return cache.versions.length
        ? cache.versions
            .map(
              (version) =>
                `<details data-td-fold="version:${esc(version.id)}"><summary><span>${esc(version.label || `成果版本 ${version.version}`)}</span><small>阶段版本 ${esc(version.version)} · ${version.files.length} 个文件</small></summary>${version.commit ? `<p class="td-context">${esc(version.commit)}</p>` : ""}${version.files.map((file) => `<div class="td-version-file"><span>${esc(file.path)}</span>${file.delivered_sha256 ? `<a href="${esc(url("/api/tasks/version-file", { version_id: version.id, path: file.path, side: "delivered" }))}" target="_blank" rel="noopener">${/\.html?$/i.test(file.path) ? "打开此版网页" : "查看此版文件"} ↗</a>` : "<small>此版本记录为删除</small>"}</div>`).join("")}<button class="btn" data-deliverable-action="preview" data-version-id="${esc(version.id)}">预览恢复影响</button></details>`,
            )
            .join("")
        : '<p class="td-empty">尚未记录阶段版本。</p>';
    }
    function render() {
      if (disposed) return;
      if (host.querySelector(".td-dialog")?.open) return;
      savePosition();
      renderer.replace(
        host,
        `<section class="td-root"><div class="td-toolbar"><button data-deliverable-action="expand">${expanded ? "回到对话旁" : "展开阅读"}</button><button data-deliverable-action="refresh" ${loading ? "disabled" : ""}>${loading ? "读取中…" : "刷新"}</button></div>
        ${errors.map((error) => `<p class="td-error" role="alert">${esc(error)}</p>`).join("")}
        ${latestMarkup()}
        ${folderMarkup()}
        <section class="td-report-section">${reportMarkup()}</section>
        <details class="td-collection" data-td-fold="versions"><summary>全部阶段版本 <small>${cache.versions.length}</small></summary>${versionsMarkup()}</details>
        <details class="td-collection" data-td-fold="files"><summary>附件与补充文件 <small>${cache.attachments.length + documents.length}</small></summary><label class="td-upload">上传到此任务<input class="td-file" type="file" multiple ${saving ? "disabled" : ""}></label><div class="td-attachment-list">${cache.attachments.map(attachment).join("") || '<p class="td-empty">尚无任务附件。</p>'}</div>
        ${documents.map((doc) => `<div class="td-document"><a href="#" data-file-path="${esc(doc.path)}" data-file-session="${esc(doc.session_id || sessionId)}" data-document-id="${esc(doc.id)}">${esc(doc.title || doc.path)}</a>${doc.summary ? `<p>${esc(doc.summary)}</p>` : ""}</div>`).join("")}
        </details>
        ${reports.length ? `<details class="td-collection" data-td-fold="history"><summary>历史交付报告 <small>${reports.length}</small></summary>${reports.map((r) => `<div class="td-document"><strong>${esc(r.title)} · 第 ${r.round} 轮 · v${r.version}</strong><p>${esc(r.summary)}</p><button data-deliverable-action="historical-report" data-report-id="${esc(r.id)}">阅读原始交付</button></div>`).join("")}</details>` : ""}
        <div class="td-dialog-host"></div></section>`,
      );
      for (const fold of host.querySelectorAll("[data-td-fold]")) {
        fold.open = cache.expanded.has(fold.dataset.tdFold);
        fold.addEventListener("toggle", () =>
          fold.open
            ? cache.expanded.add(fold.dataset.tdFold)
            : cache.expanded.delete(fold.dataset.tdFold),
        );
      }
      const area = host.closest(".tw-drawer-body");
      if (area) area.scrollTop = cache.scroll;
      void window.CrewRichDocuments?.enhance(host);
    }
    async function refresh() {
      if (disposed || loading) return;
      const token = ++generation;
      loading = true;
      errors = [];
      render();
      const paths = ["report", "attachments", "versions"];
      const results = await Promise.allSettled(
        paths.map((endpoint) => api(`/api/tasks/${endpoint}`)),
      );
      if (disposed || generation !== token) return;
      results.forEach((result, index) => {
        if (result.status === "rejected") {
          errors.push(
            `${["报告", "附件", "版本"][index]}读取失败：${result.reason.message}`,
          );
          return;
        }
        if (index === 0) {
          const report = result.value.report;
          if (cache.report && report.version !== cache.report.version)
            cache.pendingReport = report;
          else if (!cache.report) cache.report = report;
        } else if (index === 1) cache.attachments = result.value.attachments;
        else cache.versions = result.value.versions;
      });
      loading = false;
      render();
    }
    function dialog(title, body) {
      host.querySelector(".td-dialog-host").innerHTML =
        `<dialog class="td-dialog"><h2>${esc(title)}</h2>${body}<div class="td-actions"><button data-deliverable-action="dialog-close">关闭</button></div></dialog>`;
      host.querySelector(".td-dialog").showModal();
    }
    async function click(event) {
      const button = event.target.closest("[data-deliverable-action]");
      if (!button || button.disabled) return;
      const action = button.dataset.deliverableAction,
        token = generation;
      try {
        if (action === "refresh") return refresh();
        if (action === "expand") return onExpand(!expanded);
        if (action === "show-versions") {
          cache.expanded.add("versions");
          if (cache.versions[0])
            cache.expanded.add(`version:${cache.versions[0].id}`);
          render();
          host.querySelector('[data-td-fold="versions"]')?.scrollIntoView({
            block: "start",
            behavior: "auto",
          });
        } else if (action === "edit-report") {
          cache.draft ||= {
            version: cache.report?.version || 0,
            markdown: cache.report?.markdown || "",
          };
          cache.editing = true;
          storeDraft();
          render();
        } else if (action === "cancel-edit") {
          cache.editing = false;
          render();
        } else if (action === "rebase-report") {
          cache.draft.version = (cache.pendingReport || cache.report).version;
          storeDraft();
          render();
        } else if (action === "apply-report") {
          cache.report = cache.pendingReport;
          cache.pendingReport = null;
          render();
        } else if (action === "copy-folder") {
          await navigator.clipboard.writeText(root);
          toast("文件夹路径已复制。");
        } else if (action === "open-folder") {
          const result = await client.desktop.openFolder(root, {
            session_id: sessionId,
            project,
          });
          if (disposed) return;
          toast(
            result?.opened === true
              ? "已在执行电脑打开文件夹。"
              : result?.reason || "打开结果尚未确认，可以复制路径。",
          );
        } else if (action === "dialog-close") {
          host.querySelector(".td-dialog")?.close();
          render();
        } else if (action === "historical-report") {
          const item = reports.find(
            (r) => String(r.id) === button.dataset.reportId,
          );
          dialog(
            `第 ${item.round} 轮交付 · v${item.version}`,
            `<iframe class="td-historical-frame" title="历史交付原文" sandbox="allow-scripts" src="${esc(client.resourceURL(item.url))}"></iframe>`,
          );
        } else if (action === "preview") {
          button.disabled = true;
          const value = await api("/api/tasks/versions/preview", {
            task_id: taskId,
            version_id: button.dataset.versionId,
          });
          if (disposed) return;
          preview = { ...value, requestId: id() };
          dialog(
            "恢复前，核对实际文件",
            `<p>目标版本 ${esc(cache.versions.find((v) => v.id === value.version_id)?.label || value.version_id)}。只恢复下列文件，不会启动任务。</p><div class="td-restore-files">${value.files.map((file) => `<div><strong>${esc(file.path)}</strong><span>${esc({ write: "将写入此版内容", delete: "将删除", unchanged: "内容相同", conflict: "存在后续修改，不能覆盖" }[file.action] || file.action)}</span>${file.reason ? `<p>${esc(file.reason)}</p>` : ""}</div>`).join("")}</div><button class="btn danger" data-deliverable-action="restore" ${value.can_restore ? "" : "disabled"}>确认恢复这些文件</button><p class="td-restore-status" role="status"></p>`,
          );
          button.disabled = false;
        } else if (action === "restore" && preview) {
          button.disabled = true;
          const result = await api("/api/tasks/versions/restore", {
            task_id: taskId,
            version_id: preview.version_id,
            expected_files: preview.expected_files,
            request_id: preview.requestId,
          });
          if (disposed) return;
          host.querySelector(".td-restore-status").textContent =
            result.ok === false
              ? result.reason
              : "版本文件已按预览恢复；执行状态保持。";
          preview = null;
          onChanged();
        }
      } catch (error) {
        if (disposed) return;
        button.disabled = false;
        const output = host.querySelector(".td-restore-status");
        if (output && action === "restore") {
          if (error.status === 409) {
            output.textContent = `${error.message}。冲突内容已保留，请关闭后重新预览。`;
            preview = null;
            button.disabled = true;
          } else {
            output.textContent = `${error.message}。本次恢复结果尚未确认；再次核对使用同一请求编号。`;
            button.textContent = "核对同一恢复请求";
          }
        } else if (action === "copy-folder") {
          cache.expanded.add("folder");
          render();
          toast(`复制失败：${error.message}。可以手动复制展开的路径。`);
        } else toast(error.message);
      }
    }
    function input(event) {
      if (!event.target.matches(".td-report-form textarea")) return;
      cache.draft.markdown = event.target.value;
      storeDraft();
    }
    async function submit(event) {
      if (!event.target.matches(".td-report-form")) return;
      event.preventDefault();
      if (saving) return;
      saving = true;
      const form = event.target,
        submit = form.querySelector('[type="submit"]'),
        proposed = { ...cache.draft };
      submit.disabled = true;
      try {
        const result = await api("/api/tasks/report", {
          task_id: taskId,
          version: proposed.version,
          markdown: proposed.markdown,
          request_id: id(),
        });
        cache.report = result.report;
        cache.pendingReport = null;
        if (
          cache.draft?.version === proposed.version &&
          cache.draft?.markdown === proposed.markdown
        ) {
          cache.draft = null;
          cache.editing = false;
          try {
            localStorage.removeItem(draftKey);
          } catch (error) {
            if (!disposed)
              toast(`报告已保存，本地草稿清理失败：${error.message}`);
          }
        }
        if (disposed) return;
        render();
        onChanged();
        toast("报告已保存。");
      } catch (error) {
        if (disposed) return;
        form.querySelector(".td-save-error").textContent =
          `${error.message}。本地草稿保留；请返回阅读，刷新并核对新稿次后再编辑。`;
        storeDraft();
        if (error.status === 409) await refresh();
      } finally {
        saving = false;
        if (!disposed) {
          submit.disabled = false;
          const current = host.querySelector('.td-report-form [type="submit"]');
          if (current) current.disabled = false;
        }
      }
    }
    async function upload(event) {
      if (!event.target.matches(".td-file")) return;
      const files = Array.from(event.target.files);
      if (!files.length || saving) return;
      saving = true;
      event.target.disabled = true;
      try {
        for (const file of files) {
          if (disposed) break;
          const base64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () =>
              resolve(reader.result.slice(reader.result.indexOf(",") + 1));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
          });
          const result = await api("/api/tasks/attachments", {
            task_id: taskId,
            filename: file.name,
            mime_type: file.type || "application/octet-stream",
            data: base64,
            request_id: id(),
          });
          cache.attachments.push(result.attachment);
        }
        if (!disposed) {
          render();
          toast("任务附件已上传。");
          onChanged();
        }
      } catch (error) {
        if (!disposed)
          toast(`上传中断：${error.message}。已成功的附件保留，未自动重做。`);
      } finally {
        saving = false;
        if (!disposed) {
          event.target.disabled = false;
          const current = host.querySelector(".td-file");
          if (current) current.disabled = false;
        }
      }
    }
    host.addEventListener("click", click);
    host.addEventListener("input", input);
    host.addEventListener("submit", submit);
    host.addEventListener("change", upload);
    if (root && !client.synthetic)
      void client
        .request("/api/auth/session")
        .then((access) => {
          if (disposed) return;
          localFolderAvailable = Boolean(access.local);
          render();
        })
        .catch((error) => {
          if (!disposed) {
            folderAccessError = error.message;
            render();
          }
        });
    render();
    void refresh();
    return {
      refresh,
      updateContext(value) {
        documents = value.documents || [];
        reports = value.reports || [];
        taskTitle = value.taskTitle || taskTitle;
      },
      setExpanded(value) {
        expanded = value;
        const button = host.querySelector('[data-deliverable-action="expand"]');
        if (button) button.textContent = value ? "回到对话旁" : "展开阅读";
        void window.CrewRichDocuments?.enhance(host);
      },
      destroy() {
        if (disposed) return;
        savePosition();
        storeDraft();
        disposed = true;
        generation++;
        renderer.destroy();
        host.removeEventListener("click", click);
        host.removeEventListener("input", input);
        host.removeEventListener("submit", submit);
        host.removeEventListener("change", upload);
        host.innerHTML = "";
      },
    };
  }
  window.CrewTaskDeliverables = { create };
})();
