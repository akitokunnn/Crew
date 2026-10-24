"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g,c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function create(options) {
    let state = null;
    const saving = new Set(), acknowledged = new Set(), expanded = new Set(), commandExpanded = new Set(), errors = new Map();
    const pending = () => (state?.native_approvals || []).filter(item => item.status === "pending" && !acknowledged.has(String(item.id)));
    function sync(value) {
      state = value; const ids = new Set((value.native_approvals || []).map(item => String(item.id)));
      for (const id of acknowledged) if (!ids.has(id)) acknowledged.delete(id);
      for (const id of expanded) if (!ids.has(id)) { expanded.delete(id); commandExpanded.delete(id); errors.delete(id); }
    }
    function forPerson(sessionId, goalId = null, includeMembers = false) {
      const ids = new Set([sessionId, ...(includeMembers ? (state?.members || []).filter(item => item.leader_session_id === sessionId).map(item => item.session_id) : [])]);
      return pending().filter(item => ids.has(item.session_id) && (goalId == null || String(item.goal_id) === String(goalId)));
    }
    function markup(items = pending()) {
      return items.map(item => {
        const id = String(item.id), person = (state?.leaders || []).concat(state?.members || []).find(person => person.session_id === item.session_id), goal = (state?.goals || []).find(goal => String(goal.id) === String(item.goal_id));
        const command = typeof item.command === "string" ? item.command : item.command == null ? "" : JSON.stringify(item.command,null,2);
        const scope = item.kind === "fileChange" ? "仅允许本次列出的文件改动，可能访问这些路径涉及的机器资源。" : "仅本次命令离开沙箱，可能访问该命令涉及的机器资源。";
        return `<details class="native-approval" data-native-approval-id="${esc(id)}" ${expanded.has(id) ? "open" : ""}><summary><span class="native-approval-label">执行权限待确认</span><span class="native-approval-owner">${esc(person?.name || item.session_id)} · ${esc(goal?.title || (item.goal_id == null ? "总会话" : "当前任务"))}</span><span class="native-approval-expand" aria-hidden="true">查看</span></summary><div class="native-approval-body">${item.reason ? `<p><strong>原因</strong><span>${esc(item.reason)}</span></p>` : ""}<p class="native-approval-scope">${scope}不会更改默认权限；只由你确认。</p>${errors.has(id) ? `<p class="native-approval-error" role="alert">${esc(errors.get(id))}</p>` : ""}<div class="native-approval-actions"><button type="button" class="btn ghost" data-native-approval-act="deny" data-id="${esc(id)}" ${saving.has(id) ? "disabled" : ""}>拒绝</button><button type="button" class="btn" data-native-approval-act="allow" data-id="${esc(id)}" ${saving.has(id) ? "disabled" : ""}>${saving.has(id) ? "正在提交…" : "允许这一次"}</button></div><details class="native-approval-command" data-native-command-details="${esc(id)}" ${commandExpanded.has(id) ? "open" : ""}><summary>查看命令与路径</summary><div>${command ? `<label>完整命令<pre>${esc(command)}</pre></label>` : ""}${item.cwd ? `<p><strong>工作目录</strong><code>${esc(item.cwd)}</code></p>` : ""}${item.paths?.length ? `<p><strong>涉及路径</strong><span>${item.paths.map(path => esc(typeof path === "string" ? path : path.path)).join("<br>")}</span></p>` : ""}</div></details></div></details>`;
      }).join("");
    }
    async function decide(id, allow) {
      id = String(id); if (saving.has(id) || !pending().some(item => String(item.id) === id)) return;
      saving.add(id); expanded.add(id); errors.delete(id); options.onChange();
      try { await options.api("/api/native-approval", {id,allow}); acknowledged.add(id); await options.refreshState(); }
      catch (error) { errors.set(id,error.message); options.toast(error.message); }
      finally { saving.delete(id); options.onChange(); }
    }
    document.addEventListener("click", event => { const button = event.target.closest("[data-native-approval-act]"); if (!button || button.disabled) return; void decide(button.dataset.id,button.dataset.nativeApprovalAct === "allow"); });
    document.addEventListener("toggle", event => { const commandId = event.target.dataset?.nativeCommandDetails; if (commandId !== undefined) { if (event.target.open) commandExpanded.add(commandId); else commandExpanded.delete(commandId); return; } const id = event.target.dataset?.nativeApprovalId; if (id === undefined) return; if (event.target.open) expanded.add(id); else expanded.delete(id); },true);
    return {sync, markup, count:() => pending().length, countForLeader:id => forPerson(id,null,true).length, chatMarkup:(id,goalId,includeMembers) => markup(forPerson(id,goalId,includeMembers))};
  }
  window.CrewNativeApprovals = {create};
})();
