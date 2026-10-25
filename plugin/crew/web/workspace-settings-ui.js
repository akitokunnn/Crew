"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const displayName = project => project.display_name || project.name;
  const directoryKey = value => {
    const path = value.trim().replace(/\\/g, "/").replace(/\/+$/, "");
    return /^[a-z]:\//i.test(path) || path.startsWith("//") ? path.toLowerCase() : path;
  };
  const rootChanged = (root, current) => directoryKey(root) !== directoryKey(current);
  function updatePayload(snapshot, draft) {
    const display = draft.display_name.trim(), root = draft.root.trim();
    if (!display) throw Error("请填写工作区名称。");
    if (!root) throw Error("请填写工作目录。");
    return {project:snapshot.project.name, display_name:display, root, revision:snapshot.revision,
      ...(rootChanged(root, snapshot.project.root) ? {stop:true} : {})};
  }
  function create(options) {
    const host = options.host || document.body;
    const dialog = document.createElement("dialog");
    dialog.className = "workspace-settings-dialog";
    dialog.setAttribute("aria-labelledby", "workspace-settings-title");
    host.append(dialog);
    let snapshot = null, draft = null, project = null, busy = false, loading = false, confirmation = null, generation = 0, returnTo = null;
    const drafts = new Map();
    const summary = () => {
      const sessions = snapshot.sessions;
      return {total:sessions.length, running:sessions.filter(item => item.running).length, busy:sessions.filter(item => item.busy).length};
    };
    function setNote(message, failed = false) {
      const note = dialog.querySelector("[data-workspace-note]");
      note.textContent = message; note.classList.toggle("is-error", failed);
      note.setAttribute("role", failed ? "alert" : "status");
    }
    function controls(disabled) {
      for (const node of dialog.querySelectorAll("button,input")) node.disabled = disabled;
    }
    function render(message = "", failed = false) {
      const name = snapshot ? displayName(snapshot.project) : "";
      const info = snapshot ? summary() : null;
      const footer = confirmation ? `<button type="button" data-workspace-action="back">返回修改</button><button type="button" class="wss-primary ${confirmation === "remove" ? "wss-danger" : ""}" data-workspace-action="confirm">${confirmation === "remove" ? "确认从 Crew 移除" : "停止并迁移"}</button>` : `<button type="button" data-workspace-action="close">取消</button><button type="submit" form="workspace-settings-form" class="wss-primary" ${!snapshot ? "disabled" : ""}>保存工作区</button>`;
      let body;
      if (!snapshot) body = `<div class="wss-loading">${loading ? "正在读取工作区…" : '<button type="button" data-workspace-action="reload">重新读取</button>'}</div>`;
      else if (confirmation === "update") body = `<section class="wss-confirm"><h3>停止并迁移「${esc(name)}」？</h3><p>先停止 ${info.running} 个运行会话，再将全部 ${info.total} 个已有 leader 和成员迁移到新工作目录。</p><dl><dt>当前目录</dt><dd>${esc(snapshot.project.root)}</dd><dt>新目录</dt><dd>${esc(draft.root.trim())}</dd></dl><p>会保留对话和全部历史记录，不搬动磁盘文件，也不会自动重新开工。之后由你主动发送消息或开始任务。</p></section>`;
      else if (confirmation === "remove") body = `<section class="wss-confirm"><h3>从 Crew 移除「${esc(name)}」？</h3><p>将停止 ${info.running} 个运行会话，并从工作区列表移除这一项。</p><p>磁盘文件和全部记录都会保留。以后重新打开同一目录，可以恢复这个工作区。</p><dl><dt>保留的目录</dt><dd>${esc(snapshot.project.root)}</dd></dl><p>其他工作区和标签页不受影响。</p></section>`;
      else body = `<form id="workspace-settings-form"><label for="workspace-display-name">工作区名称</label><input id="workspace-display-name" name="display_name" maxlength="100" value="${esc(draft.display_name)}" autocomplete="off" required><label for="workspace-root">工作目录</label><div class="wss-directory"><input id="workspace-root" name="root" value="${esc(draft.root)}" autocomplete="off" spellcheck="false" required>${options.pickDirectory ? '<button type="button" data-workspace-action="browse">浏览…</button>' : ""}</div><p class="wss-hint">选择已有文件夹；修改登记位置，不移动磁盘文件。目录改变时，会先停止并迁移已有 leader 和成员。</p><details class="wss-sessions"><summary>${info.running ? `${info.running} 个会话在运行${info.busy ? `，其中 ${info.busy} 个正在工作` : ""}` : "当前没有运行会话"}<span>${info.total} 个已有身份 · ${snapshot.goals_count} 个目标</span></summary><ul>${snapshot.sessions.map(item => `<li><strong>${esc(item.name)}</strong><span>${item.busy ? "正在工作" : item.running ? "运行中 · 空闲" : "已停止"}</span><small title="${esc(item.cwd)}">${esc(item.cwd)}</small></li>`).join("") || '<li class="wss-hint">还没有 leader 或成员。</li>'}</ul></details></form><section class="wss-remove wss-cleanup"><div><h3>清理当前工作区</h3><p>只针对此工作区：归档全部部门、成员和旧交流后重新开始。先预览再确认，保留文件、交付成果、历史记录和账号模型设置。</p></div><button type="button" class="wss-remove-button" data-workspace-action="cleanup">清理当前工作区…</button></section><section class="wss-remove"><div><h3>从 Crew 移除</h3><p>保留磁盘文件和全部记录，重新打开同一目录可恢复。</p></div><button type="button" class="wss-remove-button" data-workspace-action="remove">从 Crew 移除</button></section>`;
      dialog.innerHTML = `<header class="wss-header"><div><span>仅此工作区</span><h2 id="workspace-settings-title">工作区设置</h2><p>${esc(name || project || "")}</p></div><button type="button" class="wss-close" data-workspace-action="close" aria-label="关闭工作区设置">×</button></header><div class="wss-body">${body}</div><footer class="wss-footer"><p data-workspace-note role="${failed ? "alert" : "status"}" class="${failed ? "is-error" : ""}">${esc(message)}</p><div>${footer}</div></footer>`;
      if (busy) controls(true);
    }
    async function read() {
      const requested = ++generation, key = project;
      loading = true; render();
      try {
        const next = await options.read(key);
        if (requested !== generation || !dialog.open) return;
        snapshot = next;
        draft = drafts.get(key) || {display_name:displayName(next.project), root:next.project.root};
        drafts.set(key, draft); loading = false; render();
        dialog.querySelector('[name="display_name"]').focus();
      } catch (error) {
        if (requested !== generation || !dialog.open) return;
        loading = false; render(error.message, true);
      }
    }
    function close() {
      if (busy) return;
      generation += 1; drafts.delete(project); draft = null; dialog.close(); options.onClose?.(project);
      if (returnTo?.isConnected) returnTo.focus({preventScroll:true});
    }
    async function save() {
      if (!snapshot || busy) return;
      let payload;
      try { payload = updatePayload(snapshot, draft); } catch (error) { setNote(error.message, true); return; }
      if (payload.stop && confirmation !== "update") { confirmation = "update"; render(); dialog.querySelector('[data-workspace-action="back"]').focus(); return; }
      busy = true; controls(true); setNote(payload.stop ? "正在停止并迁移，请稍候…" : "正在保存…");
      try {
        const next = await options.update(payload);
        snapshot = next; draft = {display_name:displayName(next.project), root:next.project.root}; drafts.set(project, draft); confirmation = null;
        busy = false; render(next.directory_change != null ? "已停止并迁移。现有身份与历史已保留，尚未重新开工。" : "工作区设置已保存。");
        options.onSaved?.(next);
      } catch (error) { busy = false; controls(false); setNote(error.message, true); }
    }
    async function remove() {
      if (!snapshot || busy) return;
      busy = true; controls(true); setNote("正在停止会话并移除工作区…");
      try {
        const result = await options.remove({project:snapshot.project.name, revision:snapshot.revision, stop:true});
        busy = false; drafts.delete(project); close(); options.onRemoved?.(result);
      } catch (error) { busy = false; controls(false); setNote(error.message, true); }
    }
    dialog.addEventListener("input", event => {
      if (!draft || busy || !["display_name", "root"].includes(event.target.name)) return;
      draft[event.target.name] = event.target.value; setNote("");
    });
    dialog.addEventListener("submit", event => { event.preventDefault(); void save(); });
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog.addEventListener("click", async event => {
      const button = event.target.closest("[data-workspace-action]");
      if (!button || busy) return;
      const action = button.dataset.workspaceAction;
      if (action === "close") close();
      else if (action === "reload") await read();
      else if (action === "back") { confirmation = null; render(); dialog.querySelector('[name="root"]').focus(); }
      else if (action === "remove") { confirmation = "remove"; render(); dialog.querySelector('[data-workspace-action="back"]').focus(); }
      else if (action === "cleanup") { const target = snapshot.project; close(); options.openCleanup(target.name, displayName(target)); }
      else if (action === "confirm") { if (confirmation === "remove") await remove(); else await save(); }
      else if (action === "browse") {
        busy = true; controls(true);
        try { const selected = await options.pickDirectory(draft.root); if (selected) { draft.root = selected; dialog.querySelector('[name="root"]').value = selected; setNote(""); } }
        catch (error) { setNote(error.message, true); }
        finally { busy = false; controls(false); button.focus(); }
      }
    });
    return {async open(name) {
      if (busy) return;
      returnTo = document.activeElement; project = name; snapshot = null; draft = null; confirmation = null;
      if (!dialog.open) dialog.showModal(); await read();
    }, close, save, getDraft:() => draft ? {...draft} : null, isOpen:() => dialog.open};
  }
  window.CrewWorkspaceSettings = {create, displayName, rootChanged, updatePayload};
})();
