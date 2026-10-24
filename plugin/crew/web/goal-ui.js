"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const labels = {queued:"待规划", running:"推进中", pausing:"暂停中", paused:"已暂停", waiting_user:"等待用户", awaiting_acceptance:"待验收", completed:"本轮已结束", failed:"未达标", cancelled:"已取消"};
  const status = value => labels[value] || value || "待规划";
  const ended = value => ["completed", "failed", "cancelled"].includes(value);
  const when = value => value ? new Date(value).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "时间未记录";
  function reportPath(project, goalId, reportId) {
    return "/api/goals/report?" + new URLSearchParams({project, goal_id:goalId, report_id:reportId});
  }
  function reportDocument(html) {
    // This policy precedes report markup. The iframe has no script, origin, form or navigation grants.
    return '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data: blob:; font-src data:; base-uri \'none\'; form-action \'none\';"><meta name="referrer" content="no-referrer">' + html;
  }
  function cards(goals, leaders = []) {
    return goals.length ? `<div class="goal-cards">${goals.map(goal => {
      const owner = leaders.find(item => item.session_id === goal.leader_session_id);
      return `<button class="goal-card" data-goal-act="open" data-goal-id="${esc(goal.id)}"><span class="goal-card-top"><span class="goal-status" data-status="${esc(goal.status)}">${esc(status(goal.status))}</span><span>第 ${esc(goal.current_round || 1)} 轮</span></span><strong>${esc(goal.title)}</strong><span class="goal-card-request">${esc(goal.request)}</span><span class="goal-card-foot">${esc(owner?.name || "负责人")} · ${esc(when(goal.updated_at))}<span>${goal.report_available ? "有交付报告 →" : ended(goal.status) ? "交付报告待齐全" : "进入目标 →"}</span></span></button>`;
    }).join("")}</div>` : '<div class="goals-empty">还没有执行目标。普通问答和讨论直接在对话里进行；明确需要交付时再建立目标。</div>';
  }
  function create(options) {
    const details = new Map(), loading = new Map(), requestedVersion = new Map(), errors = new Map();
    const drafts = new Map(), expandedGroups = new Set(), drawerPositions = new Map();
    let ownerId = null, goalId = null, drawer = null, drawerHost = null, reportId = null, reportHTML = null, reportLoading = false, reportError = "", reportGeneration = 0, reader = null, formOverlay = null, actionBusy = false;
    const state = () => options.state();
    const goals = () => state()?.goals || [];
    const byId = id => goals().find(item => String(item.id) === String(id));
    const current = () => details.get(String(goalId));
    const goal = () => {
      const listed = byId(goalId), loaded = current()?.goal;
      return loaded && (!listed || loaded.version >= listed.version) ? loaded : listed;
    };
    const owner = () => state()?.leaders.find(item => item.session_id === ownerId);
    const signature = id => { const item = byId(id); return JSON.stringify([item?.version,item?.updated_at,item?.report_available]); };
    const changed = () => options.onChange?.();
    function executionFor(id) {
      const active = goal();
      return (current()?.executions || []).filter(item => item.session_id === id && item.round === active?.current_round && item.requirement_version === active?.requirement_version).sort((a,b) => b.generation - a.generation)[0] || null;
    }
    function rememberPosition() { if (drawerHost?.dataset.viewKey) drawerPositions.set(drawerHost.dataset.viewKey, drawerHost.querySelector(".goal-drawer-scroll")?.scrollTop || 0); }
    function closeReader() { reader?.remove(); reader = null; }
    function closeDrawer() {
      rememberPosition(); drawer = null; reportId = null; reportHTML = null; reportError = ""; reportLoading = false; reportGeneration++; closeReader(); renderDrawer();
    }
    async function load(id = goalId) {
      if (id == null) return;
      const key = String(id); if (loading.has(key)) return loading.get(key);
      requestedVersion.set(key, signature(id)); errors.delete(key);
      const task = (async () => {
        try {
          const result = await options.api(`/api/goal?id=${encodeURIComponent(id)}`);
          if (!result.goal || String(result.goal.id) !== key) throw Error("目标详情不匹配，请重新读取。");
          details.set(key, result);
        } catch (error) { errors.set(key, error.message); }
        finally { loading.delete(key); if (String(goalId) === key) { changed(); renderDrawer(); } }
      })();
      loading.set(key, task); return task;
    }
    function sync(nextOwner, nextGoal) {
      if (ownerId !== nextOwner || String(goalId) !== String(nextGoal)) { closeDrawer(); ownerId = nextOwner; goalId = nextGoal; if (nextGoal != null) expandedGroups.delete(nextOwner); }
      const listed = byId(goalId), key = String(goalId);
      if (goalId != null && !loading.has(key) && (!requestedVersion.has(key) || listed && requestedVersion.get(key) !== signature(goalId))) void load();
      renderDrawer();
    }
    function groupMarkup(id) {
      const list = goals().filter(item => item.leader_session_id === id), open = expandedGroups.has(id), active = goal();
      const bar = `<div class="group-goal-bar"><button class="btn ghost ${goalId == null ? "active" : ""}" data-goal-act="general">总对话</button><button class="btn ghost" data-goal-act="list" aria-expanded="${open}">本组目标 <span>${list.length}</span></button><span class="gap"></span><button class="btn ghost" data-goal-act="create" data-leader-id="${esc(id)}">＋ 建立目标</button></div>${open ? `<div class="group-goal-list">${cards(list, state().leaders)}</div>` : ""}`;
      if (goalId == null) return bar;
      if (!active) return bar + `<section class="goal-detail"><p class="${errors.has(String(goalId)) ? "error-line" : "note"}">${esc(errors.get(String(goalId)) || "正在读取目标…")}</p><button class="btn ghost" data-goal-act="refresh">重新读取</button></section>`;
      const result = current(), reports = result?.reports || [], latest = reports[0];
      const actions = active.status === "queued" ? [["start", "开始推进"]] : ["paused","waiting_user"].includes(active.status) ? [["resume", "继续推进"]] : !ended(active.status) && active.status !== "pausing" ? [["pause", "暂停目标"]] : [];
      if (!ended(active.status)) actions.push(["cancel", "取消目标"]);
      if (ended(active.status) || active.status === "awaiting_acceptance") actions.push(["reopen", "再开一轮"]);
      return bar + `<section class="goal-detail" data-goal-detail="${esc(active.id)}"><div class="goal-detail-title"><div><span class="goal-status" data-status="${esc(active.status)}">${esc(status(active.status))}</span><span class="goal-version">第 ${esc(active.current_round || 1)} 轮 · 需求 v${esc(active.requirement_version || 1)}</span><h1>${esc(active.title)}</h1></div><div class="goal-actions">${actions.map(([action,label]) => `<button class="btn ghost" data-goal-act="action" data-action="${action}" ${actionBusy ? "disabled" : ""}>${label}</button>`).join("")}</div></div><details class="goal-requirements"><summary>原始需求与验收标准</summary><p>${esc(active.request)}</p>${active.acceptance?.length ? `<ol>${active.acceptance.map(item => `<li>${esc(typeof item === "string" ? item : item.title)}</li>`).join("")}</ol>` : '<p class="note">尚未写明验收标准</p>'}</details>${ended(active.status) ? `<p class="goal-conclusion">本轮结论：${esc(status(active.status))}。${latest ? esc(latest.title) : "HTML 交付报告尚未齐全。"}</p>` : ""}<div class="goal-materials"><button class="btn ${drawer === "reports" ? "active" : "ghost"}" data-goal-act="drawer" data-kind="reports" aria-expanded="${drawer === "reports"}">交付物${reports.length ? ` · ${reports.length}` : ""}</button><button class="btn ${drawer === "context" ? "active" : "ghost"}" data-goal-act="drawer" data-kind="context" aria-expanded="${drawer === "context"}">上下文</button><span class="goal-updated">更新于 ${esc(when(active.updated_at))}</span>${errors.has(String(goalId)) ? `<span class="error-line">${esc(errors.get(String(goalId)))}</span>` : ""}</div></section>`;
    }
    function contextMarkup() {
      const context = current()?.context || {items:[],manifest:{}}, list = context.items || [];
      return `<p class="goal-drawer-hint">这里保留需求、决定和事实的来源。纠正会保存新版本；是否已被执行采用，以应用状态和 manifest 为准。</p><div class="goal-context-items">${list.map(item => {
        const draft = drafts.get(`${goalId}:${item.id}`);
        const applied = item.status === "expired" ? "已过期" : item.status === "superseded" ? "已被新版本替代" : item.applied === true ? "已应用" : "尚未应用";
        return `<article class="goal-context-item"><div class="goal-context-meta"><span>${esc(item.kind)} · v${esc(item.version)}</span><span>${esc(applied)}</span></div><p>${esc(item.text)}</p><small>来源：${esc(item.source == null ? "未记录" : typeof item.source === "object" ? JSON.stringify(item.source) : item.source)}</small>${draft !== undefined ? `<form data-goal-context-form="${esc(item.id)}"><label>纠正后的内容<textarea name="text" rows="4" required data-goal-context-draft="${esc(item.id)}">${esc(draft)}</textarea></label><div class="goal-form-actions"><button type="button" class="btn ghost" data-goal-act="correct-close" data-item-id="${esc(item.id)}">取消</button><button class="btn" type="submit" ${actionBusy ? "disabled" : ""}>保存新版本</button></div></form>` : `<div class="goal-context-actions"><button class="text-link" data-goal-act="correct" data-item-id="${esc(item.id)}">纠正</button><button class="text-link" data-goal-act="expire" data-item-id="${esc(item.id)}" ${item.status === "expired" || actionBusy ? "disabled" : ""}>标为过期</button></div>`}</article>`;
      }).join("") || '<p class="note">还没有保存目标工作记录。</p>'}</div><form class="goal-context-add" data-goal-context-add><label>补充一条事实<textarea name="text" rows="3" required placeholder="仅保存当前目标的事实；需求变更请在对话里说明" data-goal-add-draft>${esc(drafts.get(`${goalId}:add`) || "")}</textarea></label><button class="btn" type="submit" ${actionBusy ? "disabled" : ""}>保存事实</button></form><details class="goal-manifest"><summary>本次采用的上下文 manifest</summary><pre>${esc(JSON.stringify(context.manifest || {}, null, 2))}</pre></details>`;
    }
    function renderDrawer() {
      drawerHost = document.getElementById("goal-drawer"); if (!drawerHost) return;
      drawerHost.hidden = !drawer || goalId == null;
      if (drawerHost.hidden) { delete drawerHost.dataset.markup; return; }
      const active = goal(), result = current();
      const body = !result ? `<p class="note">${esc(errors.get(String(goalId)) || "正在读取…")}</p><button class="btn" data-goal-act="refresh">重试读取</button>` : drawer === "context" ? contextMarkup() : `<p class="goal-drawer-hint">每轮报告保留历史版本。报告应包含验收证据、可视化和具体 example；打开报告不会调用模型。</p><div class="goal-report-list">${(result.reports || []).map(report => `<button class="goal-report-card ${String(reportId) === String(report.id) ? "active" : ""}" data-goal-act="report" data-report-id="${esc(report.id)}"><strong>${esc(report.title)}</strong><span>第 ${esc(report.round)} 轮 · ${esc(status(report.status))} · ${esc(when(report.created_at))}</span></button>`).join("") || '<p class="note">还没有 HTML 交付报告。</p>'}</div>${reportId == null ? "" : `<div class="goal-report-preview"><div class="goal-report-toolbar"><button class="btn ghost" data-goal-act="expand-report" ${reportHTML == null ? "disabled" : ""}>展开到主区</button><span class="note">隔离预览 · 网络与脚本已禁用</span></div>${reportLoading ? '<p class="note">正在读取报告…</p>' : reportError ? `<p class="error-line">${esc(reportError)}</p>` : '<iframe class="goal-report-frame" title="目标交付报告预览" sandbox="" referrerpolicy="no-referrer"></iframe>'}</div>`}`;
      const markup = `<div class="goal-drawer-head"><div><span>${esc(owner()?.name || "本组")} / ${esc(active?.title || goalId)}</span><h2 tabindex="-1">${drawer === "context" ? "它记得什么" : "交付物"}</h2></div><button class="icon-btn" data-goal-act="close-drawer" aria-label="关闭资料抽屉">×</button></div><div class="goal-drawer-scroll">${body}</div>`;
      if (drawerHost.dataset.markup !== markup) {
        rememberPosition();
        const viewKey = `${goalId}:${drawer}`, oldScroll = drawerPositions.get(viewKey) || 0;
        const focused = document.activeElement, draftId = focused?.dataset?.goalContextDraft, adding = focused?.hasAttribute?.("data-goal-add-draft"), selection = focused && "selectionStart" in focused ? [focused.selectionStart,focused.selectionEnd] : null;
        drawerHost.innerHTML = markup; drawerHost.dataset.markup = markup; drawerHost.dataset.viewKey = viewKey;
        drawerHost.querySelector(".goal-drawer-scroll").scrollTop = oldScroll;
        if (draftId != null || adding) { const input = [...drawerHost.querySelectorAll("textarea")].find(item => adding ? item.hasAttribute("data-goal-add-draft") : item.dataset.goalContextDraft === draftId); if (input) { input.focus({preventScroll:true}); if (selection) input.setSelectionRange(...selection); } }
      }
      const frame = drawerHost.querySelector("iframe"); if (frame && reportHTML != null && frame.dataset.report !== String(reportGeneration)) { frame.srcdoc = reportDocument(reportHTML); frame.dataset.report = String(reportGeneration); }
    }
    async function openReport(id) {
      reportId = id; reportHTML = null; reportError = ""; reportLoading = true; const generation = ++reportGeneration, selectedGoal = goalId; renderDrawer();
      try {
        const response = await fetch(reportPath(options.project, selectedGoal, id), {headers:{Accept:"text/html"}});
        if (!response.ok) throw Error("报告暂时无法读取，请重试。");
        const html = await response.text(); if (generation !== reportGeneration) return; reportHTML = html;
      } catch (error) { if (generation === reportGeneration) reportError = error.message; }
      finally { if (generation === reportGeneration) { reportLoading = false; renderDrawer(); } }
    }
    function expandReport() {
      if (reportHTML == null) return; closeReader();
      reader = document.createElement("section"); reader.className = "goal-report-reader"; reader.setAttribute("aria-label", "目标交付报告");
      reader.innerHTML = `<div class="head"><div><div class="title">${esc(goal()?.title)} · 交付报告</div><div class="sub">${esc(owner()?.name)}组 · 隔离预览</div></div><button class="btn" data-goal-act="close-report">返回目标对话</button></div><iframe title="目标完整交付报告" sandbox="" referrerpolicy="no-referrer"></iframe>`;
      document.getElementById("main").append(reader); reader.querySelector("iframe").srcdoc = reportDocument(reportHTML); reader.querySelector("button").focus();
    }
    function showCreate(id) {
      const person = state().leaders.find(item => item.session_id === id); if (!person) return;
      formOverlay?.remove(); formOverlay = document.createElement("div"); formOverlay.className = "session-config-backdrop";
      formOverlay.innerHTML = `<form class="session-config-dialog goal-create-form" data-goal-create="${esc(id)}" role="dialog" aria-modal="true" aria-labelledby="goal-create-title"><div class="session-config-head"><h2 id="goal-create-title">${esc(person.name)}组 · 建立目标</h2><button class="icon-btn" type="button" data-goal-act="close-create" aria-label="关闭">×</button></div><p class="session-config-description">明确需要实施和交付的工作才建立目标。普通问答、解释和小补充直接在对话里说。</p><label>目标标题<input name="title" required maxlength="200"></label><label>原始需求<textarea name="request" required rows="5"></textarea></label><label>验收标准 <span class="note">每行一项，可由 leader 后续完善</span><textarea name="acceptance" rows="3"></textarea></label><p class="error-line" role="alert"></p><div class="session-config-actions"><button class="btn ghost" type="button" data-goal-act="close-create">取消</button><button class="btn primary" type="submit">建立目标</button></div></form>`;
      document.body.append(formOverlay); formOverlay.querySelector("input").focus();
    }
    async function contextAction(action, itemId, text) {
      const selectedGoal = goalId, item = current()?.context?.items?.find(entry => String(entry.id) === String(itemId));
      actionBusy = true; renderDrawer();
      try {
        const result = await options.api("/api/goals/context", {goal_id:selectedGoal, action, ...(item ? {item_id:item.id, version:item.version} : {kind:"fact", source:{type:"human",id:selectedGoal}}), ...(text === undefined ? {} : {text})});
        const entry = details.get(String(selectedGoal)); if (entry) entry.context = result.context || result;
        drafts.delete(`${selectedGoal}:${itemId ?? "add"}`); options.toast("上下文已保存；执行采用情况请查看应用状态。");
      } catch (error) { options.toast(error.message); if (error.status === 409) void load(selectedGoal); }
      finally { actionBusy = false; renderDrawer(); }
    }
    document.addEventListener("input", event => {
      if (event.target.dataset.goalContextDraft !== undefined) drafts.set(`${goalId}:${event.target.dataset.goalContextDraft}`, event.target.value);
      if (event.target.hasAttribute("data-goal-add-draft")) drafts.set(`${goalId}:add`, event.target.value);
    });
    document.addEventListener("submit", async event => {
      const form = event.target;
      if (form.hasAttribute("data-goal-context-add") || form.hasAttribute("data-goal-context-form")) { event.preventDefault(); if (!actionBusy) void contextAction(form.hasAttribute("data-goal-context-add") ? "add" : "correct", form.dataset.goalContextForm, form.elements.text.value.trim()); return; }
      if (!form.hasAttribute("data-goal-create")) return; event.preventDefault(); const button = form.querySelector('[type="submit"]'); if (button.disabled) return; button.disabled = true;
      const overlay = formOverlay;
      try {
        const result = await options.api("/api/goals/create", {leader_session_id:form.dataset.goalCreate, title:form.elements.title.value.trim(), request:form.elements.request.value.trim(), acceptance:form.elements.acceptance.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)});
        await options.refreshState(); overlay?.remove(); if (formOverlay === overlay) formOverlay = null; options.onNavigate(result.goal.id);
      } catch (error) { form.querySelector(".error-line").textContent = error.message; }
      finally { button.disabled = false; }
    });
    document.addEventListener("click", async event => {
      const button = event.target.closest("[data-goal-act]"); if (!button || button.disabled) return;
      const act = button.dataset.goalAct;
      if (act === "open") { expandedGroups.delete(byId(button.dataset.goalId)?.leader_session_id); options.onNavigate(button.dataset.goalId); }
      else if (act === "general") options.onGeneral(ownerId);
      else if (act === "list") { if (expandedGroups.has(ownerId)) expandedGroups.delete(ownerId); else expandedGroups.add(ownerId); changed(); }
      else if (act === "create") showCreate(button.dataset.leaderId);
      else if (act === "close-create") { formOverlay?.remove(); formOverlay = null; }
      else if (act === "drawer") { if (drawer === button.dataset.kind) closeDrawer(); else { options.onDrawer?.(); closeReader(); drawer = button.dataset.kind; reportId = null; reportHTML = null; reportGeneration++; renderDrawer(); drawerHost.querySelector("h2").focus(); } changed(); }
      else if (act === "close-drawer") { closeDrawer(); changed(); }
      else if (act === "refresh") void load();
      else if (act === "report") void openReport(button.dataset.reportId);
      else if (act === "expand-report") expandReport();
      else if (act === "close-report") closeReader();
      else if (act === "correct") { const item = current()?.context?.items.find(item => String(item.id) === button.dataset.itemId); if (item) { drafts.set(`${goalId}:${item.id}`, item.text); renderDrawer(); } }
      else if (act === "correct-close") { drafts.delete(`${goalId}:${button.dataset.itemId}`); renderDrawer(); }
      else if (act === "expire" && !actionBusy) void contextAction("expire", button.dataset.itemId);
      else if (act === "action" && !actionBusy) {
        const active = goal(); if (!active) return;
        actionBusy = true; changed();
        try { await options.api("/api/goals/action", {goal_id:active.id,action:button.dataset.action,version:active.version}); await options.refreshState(); await load(active.id); }
        catch (error) { options.toast(error.message); if (error.status === 409) void load(active.id); }
        finally { actionBusy = false; changed(); }
      }
    });
    function escape() {
      if (formOverlay) { formOverlay.remove(); formOverlay = null; }
      else if (reader) closeReader();
      else if (drawer) { closeDrawer(); changed(); }
      else return false;
      return true;
    }
    document.addEventListener("keydown", event => {
      if (event.key !== "Tab" || !formOverlay) return;
      const items = [...formOverlay.querySelectorAll('button:not([disabled]),input,textarea')], first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    return {sync, closeDrawer, escape, groupMarkup, cards:() => cards(goals(), state()?.leaders), refresh:load, currentGoal:goal, executionFor,
      invalidate() { requestedVersion.clear(); details.clear(); }};
  }
  window.CrewGoals = {create, cards, status, reportPath, reportDocument};
})();
