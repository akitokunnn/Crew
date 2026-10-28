"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const labels = {queued:"待规划", running:"推进中", pausing:"暂停中", paused:"已暂停", waiting_user:"等待用户", awaiting_acceptance:"待验收", completed:"本轮已结束", failed:"未达标", cancelled:"已取消"};
  // Report outcomes and acceptance states are what the AI wrote in its report, not a human or independent verdict.
  const outcomes = {progress:"阶段成果",completed:"已达标", failed:"未达标", awaiting_acceptance:"等待用户验收", cancelled:"已取消"};
  const acceptanceLabels = [["passed","通过"],["failed","失败"],["unverified","未验证"],["simulated","模拟验证"]];
  const status = value => labels[value] || value || "待规划";
  const ended = value => ["completed", "failed", "cancelled"].includes(value);
  const when = value => value ? new Date(value).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "时间未记录";
  function reportPath(project, goalId, reportId) {
    return "/api/goals/report?" + new URLSearchParams({project, goal_id:goalId, report_id:reportId});
  }
  function reportDocument(html) {
    // Match report_documents.VIEWER_POLICY. Inline charts run without origin, forms or top navigation.
    return '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob:; font-src data:; media-src data:; base-uri \'none\'; form-action \'none\'; frame-src about:;"><meta name="referrer" content="no-referrer">' + html;
  }
  function cards(goals, leaders = [], selected = null) {
    return goals.length ? `<div class="goal-cards">${goals.map(goal => {
      const owner = leaders.find(item => item.session_id === goal.leader_session_id);
      return `<article class="goal-card"><button class="goal-card-open" data-goal-act="open" data-goal-id="${esc(goal.id)}" aria-pressed="${String(selected) === String(goal.id)}"><span class="goal-card-top"><span class="goal-status" data-status="${esc(goal.status)}">${esc(status(goal.status))}</span><span>第 ${esc(goal.current_round || 1)} 轮</span></span><strong>${esc(goal.title)}</strong><span class="goal-card-request">${esc(goal.request)}</span>${goal.work_summary?.total ? `<span class="goal-work-progress">已完成 ${goal.work_summary.done} / ${goal.work_summary.total}<progress max="${goal.work_summary.total}" value="${goal.work_summary.done}" aria-label="AI 工作清单进度"></progress></span>` : ""}<span class="goal-card-foot">${esc(owner?.name || "负责人")} · ${esc(when(goal.updated_at))}<span>${ended(goal.status) && !goal.report_available ? "交付报告待齐全" : "进入任务 →"}</span></span></button>${goal.report_available ? `<button class="goal-card-delivery" data-goal-act="delivery" data-goal-id="${esc(goal.id)}" aria-label="查看「${esc(goal.title)}」的交付报告">查看交付报告<span aria-hidden="true">→</span></button>` : ""}</article>`;
    }).join("")}</div>` : '<div class="goals-empty">还没有执行目标。普通问答和讨论直接在对话里进行；明确需要交付时再建立目标。</div>';
  }
  // A report's relation is recomputed from the newest goal state, so an open report follows later round,
  // requirement or report changes. Reports arrive newest first by round and saved report version.
  const isCurrent = (report, goal) => report.round === goal.current_round && report.requirement_version === goal.requirement_version;
  const currentReport = (reports, goal) => reports.find(item => isCurrent(item, goal)) || null;
  function reportScope(report, goal, reports) {
    if (isCurrent(report, goal)) {
      const newer = reports.find(item => isCurrent(item, goal) && item.version > report.version);
      return newer ? {label:"本轮较早报告", current:false, note:`已有更新版本（第 ${newer.version} 稿）`} : {label:"本轮报告", current:true, note:""};
    }
    if (currentReport(reports, goal)) return {label:"历史报告", current:false, note:"本轮已有新报告"};
    return {label:"历史报告", current:false, note:report.round < goal.current_round ? `当前第 ${goal.current_round} 轮暂无新交付` : `需求已更新到 v${goal.requirement_version}，本轮暂无新交付`};
  }
  function create(options) {
    const details = new Map(), loading = new Map(), requestedVersion = new Map(), errors = new Map();
    const documentLoads = new Map(), documentErrors = new Map();
    const drafts = new Map(), expandedGroups = new Set(), drawerPositions = new Map(), conclusionsOpen = new Set(), reportNotesOpen = new Set();
    let pendingAction = "", actionError = "";
    let ownerId = null, goalId = null, drawer = null, drawerHost = null, reportId = null, reportHTML = null, reportLoading = false, reportError = "", reportGeneration = 0, reader = null, formOverlay = null, actionBusy = false;
    // reading: the report in the main-area reader. Mode "drawer" returns to the materials drawer; "direct" was
    // opened by an explicit delivery click and returns to the task. covered: task-area nodes made inert meanwhile.
    let reading = null, deliveryNotice = null;
    const covered = new Set();
    const state = () => options.state();
    const goals = () => state()?.goals || [];
    const byId = id => goals().find(item => String(item.id) === String(id));
    const current = () => details.get(String(goalId));
    const goal = () => {
      const listed = byId(goalId), loaded = current()?.goal;
      return loaded && (!listed || loaded.version >= listed.version) ? loaded : listed;
    };
    const owner = () => state()?.leaders.find(item => item.session_id === ownerId);
    const signature = id => { const item = byId(id); return JSON.stringify([item?.version,item?.updated_at,item?.report_available,item?.work_summary?.revision,item?.decision_mode?.version,item?.native_handoffs]); };
    const changed = () => options.onChange?.();
    function restoreFocus(selector) {
      const target = document.querySelector?.(selector);
      if (target?.closest?.('#side-task-section')) options.onReturn?.();
      (document.querySelector?.(selector) || document.querySelector?.('[data-act="task-panel"]'))?.focus();
    }
    function executionFor(id) {
      const active = goal();
      return (current()?.executions || []).filter(item => item.session_id === id && item.round === active?.current_round && item.requirement_version === active?.requirement_version).sort((a,b) => b.generation - a.generation)[0] || null;
    }
    function rememberPosition() { if (drawerHost?.dataset.viewKey) drawerPositions.set(drawerHost.dataset.viewKey, drawerHost.querySelector(".goal-drawer-scroll")?.scrollTop || 0); }
    function closeReader() { options.onReader?.(false); reader?.remove(); reader = null; reading = null; for (const node of covered) node.inert = false; covered.clear(); }
    // The reader covers the task area: nothing behind it may take focus or input. The sidebar stays usable; onReader makes the floating auxiliary panels inert.
    function cover() { for (const node of document.getElementById("main").children) if (node !== reader && !node.inert) { node.inert = true; covered.add(node); } }
    function closeDrawer() {
      rememberPosition(); drawer = null; reportId = null; reportHTML = null; reportError = ""; reportLoading = false; reportGeneration++; deliveryNotice = null; closeReader(); renderDrawer();
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
        finally { loading.delete(key); if (String(goalId) === key) { changed(); renderMaterials(); } }
      })();
      loading.set(key, task); return task;
    }
    async function loadDocuments(id = goalId) {
      if (id == null) return; const key = String(id); if (documentLoads.has(key)) return documentLoads.get(key);
      documentErrors.delete(key);
      const task = (async () => { try { const result = await options.api(`/api/documents?goal_id=${encodeURIComponent(id)}`); if (!Array.isArray(result.documents)) throw Error("任务文档暂时无法读取，请重试。"); const entry = details.get(key); if (entry) entry.documents = result.documents; }
        catch (error) { documentErrors.set(key,error.message); }
        finally { documentLoads.delete(key); if (String(goalId) === key) renderDrawer(); } })();
      documentLoads.set(key,task); if (String(goalId) === key) renderDrawer(); return task;
    }
    function sync(nextOwner, nextGoal) {
      if (ownerId !== nextOwner || String(goalId) !== String(nextGoal)) { closeDrawer(); actionError = ""; ownerId = nextOwner; goalId = nextGoal; if (nextGoal != null) expandedGroups.delete(nextOwner); }
      const listed = byId(goalId), key = String(goalId);
      if (goalId != null && !loading.has(key) && (!requestedVersion.has(key) || listed && requestedVersion.get(key) !== signature(goalId))) void load();
      renderMaterials();
    }
    function renderMaterials() { renderDrawer(); if (reader) renderReader(); }
    function deliveryMarkup(active, reports) {
      const key = String(active.id);
      const notice = deliveryNotice?.goalId !== key ? "" : deliveryNotice.error == null ? '<p class="goal-delivery-status note" role="status">正在打开交付报告…</p>'
        : `<p class="goal-action-error error-line" role="alert">交付报告未能打开：${esc(deliveryNotice.error)} <button class="text-link" data-goal-act="delivery" data-goal-id="${esc(key)}">重试</button></p>`;
      if (!reports.length) return notice;
      const report = currentReport(reports, active);
      if (!report) return `<div class="goal-delivery goal-delivery-gap"><span>${esc(reportScope(reports[0], active, reports).note)}。</span><button class="text-link" data-goal-act="delivery" data-goal-id="${esc(key)}">查看历史报告（第 ${esc(reports[0].round)} 轮）</button></div>${notice}`;
      const open = conclusionsOpen.has(`${key}:${report.id}`), counts = report.acceptance_counts, unresolved = report.unresolved;
      const recorded = acceptanceLabels.filter(([name]) => counts[name] > 0);
      const total = recorded.reduce((sum, [name]) => sum + counts[name], 0);
      const notesOpen = reportNotesOpen.has(`${key}:${report.id}`);
      const folds = report.summary.length > 40 || report.summary.includes("\n");
      const notes = unresolved.length ? `<div class="goal-delivery-notes"><button class="text-link" data-goal-act="report-notes-toggle" data-report-id="${esc(report.id)}" aria-expanded="${notesOpen}">报告补充说明 · ${unresolved.length} 条 · ${notesOpen ? "收起" : "展开"}</button>${notesOpen ? `<p>以下为报告原文中的遗留问题或限制，不作为额外验收项计数。</p><ul class="goal-delivery-unresolved-list">${unresolved.map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</div>` : "";
      return `<section class="goal-delivery" aria-label="本轮交付结论"><div class="goal-delivery-head"><strong>AI 提交的结论</strong><span>第 ${esc(report.round)} 轮报告 · 报告结果：${esc(outcomes[report.status])}</span></div><p class="goal-delivery-summary ${open ? "open" : ""}">${esc(report.summary)}</p><div class="goal-delivery-foot"><div class="goal-delivery-meta"><span>报告验收记录（AI 填写）</span>${recorded.length ? `<span class="goal-acceptance-total">共 ${esc(total)} 项</span>${recorded.map(([name,label]) => `<span class="goal-acceptance-count" data-kind="${name}">${label} ${esc(counts[name])}</span>`).join("")}` : "<span>没有逐项验收记录</span>"}</div><div class="goal-delivery-actions">${folds ? `<button class="text-link" data-goal-act="conclusion-toggle" data-report-id="${esc(report.id)}" aria-expanded="${open}">${open ? "收起" : "展开全文"}</button>` : ""}<button class="btn ghost" data-goal-act="delivery" data-goal-id="${esc(key)}">查看完整报告</button></div></div>${notes}</section>${notice}`;
    }
    function groupMarkup(id) {
      const list = goals().filter(item => item.leader_session_id === id), open = expandedGroups.has(id), active = goal();
      const tasksSelected = open || goalId != null;
      const bar = `<div class="group-goal-bar"><div class="goal-view-tabs" role="tablist" aria-label="本组工作视图"><button class="btn ghost ${!tasksSelected ? "active" : ""}" role="tab" aria-selected="${!tasksSelected}" tabindex="${!tasksSelected ? 0 : -1}" data-goal-act="general">总会话</button><button class="btn ghost ${tasksSelected ? "active" : ""}" role="tab" aria-selected="${tasksSelected}" tabindex="${tasksSelected ? 0 : -1}" data-goal-act="list">任务清单 <span>${list.length}</span></button></div><span class="gap"></span><button class="btn ghost" data-goal-act="create" data-leader-id="${esc(id)}">＋ 建立目标</button></div>`;
      if (open) return bar + `<section class="group-goal-list" role="tabpanel" aria-label="本组任务清单">${cards(list, state().leaders, goalId)}</section>`;
      if (goalId == null) return bar;
      if (!active) return bar + `<section class="goal-detail"><p class="${errors.has(String(goalId)) ? "error-line" : "note"}">${esc(errors.get(String(goalId)) || "正在读取目标…")}</p><button class="btn ghost" data-goal-act="refresh">重新读取</button></section>`;
      return `<section class="goal-detail goal-compact" data-goal-detail="${esc(active.id)}"><div class="goal-detail-title"><button class="btn ghost" type="button" data-goal-act="general">总会话</button><span class="goal-path-separator">/</span><span class="goal-status" data-status="${esc(active.status)}">${esc(status(active.status))}</span><h1 title="${esc(active.title)}">${esc(active.title)}</h1><div class="goal-mode-control" role="group" aria-label="本任务决策方式">${[["human","由我拍板"],["autonomous","自主推进"]].map(([mode,label]) => `<button type="button" data-goal-act="mode" data-mode="${mode}" aria-pressed="${(active.decision_mode?.mode || "human") === mode}" title="仅改变此任务的决策方式，不启动执行" ${actionBusy ? "disabled" : ""}>${label}</button>`).join("")}</div><button class="goal-list-back" type="button" data-goal-act="list" aria-label="返回任务清单">换任务 ▾</button>${actionError || errors.has(String(goalId)) ? '<button class="text-link goal-error-indicator" data-act="task-panel">查看任务提示</button>' : ""}</div></section>`;
    }
    function sideMarkup(id = ownerId) {
      if (goalId == null || expandedGroups.has(id)) {
        const list = goals().filter(item => item.leader_session_id === id);
        return `<div class="goal-quick-list">${list.map(item => `<button type="button" class="side-task-row" data-goal-act="open" data-goal-id="${esc(item.id)}" aria-current="${String(item.id) === String(goalId) ? "true" : "false"}"><span class="goal-status" data-status="${esc(item.status)}">${esc(status(item.status))}</span><span>${esc(item.title)}</span><span aria-hidden="true">›</span></button>`).join("") || '<p class="note">本组还没有任务。</p>'}</div><div class="side-task-links"><button class="text-link" data-goal-act="list">查看完整清单</button><button class="text-link" data-goal-act="create" data-leader-id="${esc(id)}">建立目标</button></div>`;
      }
      const active = goal();
      if (!active) return `<p class="note">${esc(errors.get(String(goalId)) || "正在读取任务…")}</p><button class="btn ghost" data-goal-act="refresh">重新读取</button>`;
      const result = current(), reports = result?.reports || [], review = result?.acceptance_review || result?.previous_acceptance_review;
      const followup = ["awaiting_acceptance","completed","failed"].includes(active.status) ? '<p class="goal-followup-hint">可以直接在下方继续聊、补充要求或修改报告。发送后继续本任务的新一轮，已有报告与验收记录保留。</p>' : "";
      const actions = active.status === "awaiting_acceptance" ? [["accept","验收通过"],["reject","需要修改"]] : active.status === "queued" ? [["start", "开始推进"]] : ["paused","waiting_user"].includes(active.status) ? [["resume", "继续推进"]] : !ended(active.status) && active.status !== "pausing" ? [["pause", "暂停目标"]] : [];
      if (!ended(active.status) && active.status !== "awaiting_acceptance") actions.push(["cancel", "取消目标"]);
      if (ended(active.status)) actions.push(["reopen", "再开一轮"]);
      return `<section class="goal-tools" data-goal-tools="${esc(active.id)}"><div class="goal-detail-title"><span class="goal-status" data-status="${esc(active.status)}">${esc(status(active.status))}</span><h1 title="${esc(active.title)}">${esc(active.title)}</h1><span class="goal-version">第 ${esc(active.current_round || 1)} 轮</span></div><div class="goal-actions">${actions.map(([action,label]) => `<button class="btn ${["start","resume","reopen","accept"].includes(action) ? "goal-primary-action" : "ghost"}" data-goal-act="action" data-action="${action}" ${actionBusy ? "disabled" : ""}>${pendingAction === action ? "正在处理…" : label}</button>`).join("")}</div>${window.CrewGoalWork?.markup(current()?.work_items || [], [...(state()?.leaders || []), ...(state()?.members || [])]) || ""}<div class="goal-materials"><button class="btn ghost" data-goal-act="drawer" data-kind="requirements" aria-controls="goal-drawer" aria-expanded="${drawer === "requirements"}">需求与验收</button><button class="btn ghost" data-goal-act="drawer" data-kind="reports" aria-controls="goal-drawer" aria-expanded="${drawer === "reports"}">交付与文档${reports.length ? ` · ${reports.length}` : ""}</button><button class="btn ghost" data-goal-act="drawer" data-kind="context" aria-controls="goal-drawer" aria-expanded="${drawer === "context"}">上下文</button><button class="text-link" data-goal-act="versions">成果版本</button><span class="goal-updated">需求 v${esc(active.requirement_version || 1)}</span></div>${followup}${deliveryMarkup(active, reports)}${review ? `<details class="goal-review"><summary>${review.round < active.current_round ? `第 ${esc(review.round)} 轮 · ` : ""}${review.accepted ? "你已验收通过" : "你退回修改"}${review.note ? `：${esc(review.note.slice(0,80))}${review.note.length > 80 ? "…" : ""}` : ""}</summary><p>${esc(review.note || "你确认当前轮交付符合验收要求。")}<br><span>原始交付报告保持生成时的内容和状态。</span></p></details>` : ""}${actionError || errors.has(String(goalId)) && deliveryNotice?.goalId !== String(goalId) ? `<p class="goal-action-error error-line" role="alert">${esc(actionError || errors.get(String(goalId)))}</p>` : ""}</section>`;
    }
    function requirementsMarkup(active) {
      return `<section class="goal-requirements"><h3>${esc(active.title)}</h3><strong>原始需求与验收标准</strong><p>${esc(active.request)}</p>${active.acceptance?.length ? `<ol>${active.acceptance.map(item => `<li>${esc(typeof item === "string" ? item : item.title)}</li>`).join("")}</ol>` : '<p class="note">尚未写明验收标准</p>'}${active.constraints?.length ? `<strong>约束</strong><ul>${active.constraints.map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</section>`;
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
      const body = !result ? `<p class="note">${esc(errors.get(String(goalId)) || "正在读取…")}</p><button class="btn" data-goal-act="refresh">重试读取</button>` : drawer === "requirements" ? requirementsMarkup(active) : drawer === "context" ? contextMarkup() : `<p class="goal-drawer-hint">仅收录当前任务的交付报告和相关文档。打开资料不会调用模型。</p>${result.acceptance_review ? `<p class="goal-drawer-hint">${result.acceptance_review.accepted ? "你已验收通过" : "你退回修改"}${result.acceptance_review.note ? `：${esc(result.acceptance_review.note)}` : ""}。原始报告保持生成时的内容和状态。</p>` : ""}<h3 class="goal-material-heading">交付报告</h3><div class="goal-report-list">${(result.reports || []).map(report => `<button class="goal-report-card ${String(reportId) === String(report.id) ? "active" : ""}" data-goal-act="report" data-report-id="${esc(report.id)}"><strong>${esc(report.title)}</strong><span>第 ${esc(report.round)} 轮 · 第 ${esc(report.version)} 稿 · ${reportScope(report, active, result.reports).label} · AI 报告结果：${esc(outcomes[report.status])} · ${esc(when(report.created_at))}</span></button>`).join("") || '<p class="note">还没有 HTML 交付报告。</p>'}</div>${reportId == null ? "" : `<div class="goal-report-preview"><div class="goal-report-toolbar"><button class="btn ghost" data-goal-act="expand-report" ${reportHTML == null ? "disabled" : ""}>展开到主区</button><span class="note">隔离预览 · 支持图表交互</span></div>${reportLoading ? '<p class="note">正在读取报告…</p>' : reportError ? `<p class="error-line">${esc(reportError)}</p>` : '<iframe class="goal-report-frame" title="目标交付报告预览" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>'}</div>`}<h3 class="goal-material-heading">相关文档</h3><div class="goal-related-documents">${options.renderDocuments?.(result.documents || [], goalId) || '<p class="note">这个任务还没有相关文档。</p>'}</div>`;
      const markup = `<div class="goal-drawer-head"><div><span>${esc(owner()?.name || "本组")} / ${esc(active?.title || goalId)}</span><h2 tabindex="-1">${({requirements:"需求与验收",context:"上下文",reports:"交付与文档"})[drawer]}</h2></div><button class="icon-btn" data-goal-act="refresh" aria-label="刷新当前任务资料" ${documentLoads.has(String(goalId)) ? "disabled" : ""}>↻</button><button class="icon-btn" data-goal-act="close-drawer" aria-label="关闭资料抽屉">×</button></div><div class="goal-drawer-scroll">${documentLoads.has(String(goalId)) && drawer === "reports" ? '<p class="note" role="status">正在读取任务文档…</p>' : ""}${documentErrors.has(String(goalId)) && drawer === "reports" ? `<p class="error-line" role="alert">${esc(documentErrors.get(String(goalId)))}</p>` : ""}${body}</div>`;
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
    function readerHeadMarkup() {
      const active = goal(), report = reading.report, scope = reportScope(report, active, reading.reports);
      return `<div class="goal-reader-heading"><div class="title">${esc(active.title)} · 交付报告</div><div class="sub goal-reader-meta"><span class="goal-report-scope" data-current="${scope.current}">${scope.label}</span><span>第 ${esc(report.round)} 轮 · 需求 v${esc(report.requirement_version)} · 第 ${esc(report.version)} 稿</span><span>AI 报告结果：${esc(outcomes[report.status])}</span>${scope.note ? `<span class="goal-reader-gap">${esc(scope.note)}</span>` : ""}<span>${esc(owner()?.name)}组 · 隔离预览</span></div></div>${report.status === "progress" && scope.current && !["completed","cancelled","awaiting_acceptance"].includes(active.status) ? `<button class="btn ghost" data-goal-act="edit-stage" data-report-id="${esc(report.id)}">编辑阶段说明</button>` : ""}<button class="btn" data-goal-act="close-report">${reading.mode === "direct" ? "返回任务" : "返回交付资料"}</button>`;
    }
    // Header and status follow the latest goal detail; the report document is written once, so later updates
    // never reload the iframe or lose the reading position.
    function renderReader() {
      if (current()) reading.reports = current().reports;
      const focused = reader.contains?.(document.activeElement), head = reader.querySelector(".goal-reader-head"), markup = readerHeadMarkup();
      if (head.dataset.markup !== markup) { head.innerHTML = markup; head.dataset.markup = markup; }
      const body = reader.querySelector(".goal-reader-body"), status = reading.loading ? '<p class="note goal-reader-status" role="status">正在读取报告…</p>'
        : reading.error ? `<div class="goal-reader-status"><p class="error-line" role="alert">${esc(reading.error)}</p><button class="btn" data-goal-act="retry-report">重新读取报告</button></div>` : "";
      if (body.dataset.markup !== status) { body.innerHTML = status; body.dataset.markup = status; body.hidden = !status; }
      const frame = reader.querySelector(".goal-reader-frame");
      if (reading.html != null && frame.hidden) { frame.innerHTML = '<iframe title="目标完整交付报告" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>'; frame.querySelector("iframe").srcdoc = reportDocument(reading.html); frame.hidden = false; }
      if (focused && !reader.contains(document.activeElement)) head.querySelector('[data-goal-act="close-report"]').focus();
    }
    function openReader(next) {
      options.onOverlay?.(); options.onReader?.(true);
      reading = next; reader = document.createElement("section"); reader.className = "goal-report-reader"; reader.setAttribute("aria-label", "目标交付报告");
      reader.innerHTML = '<div class="head goal-reader-head"></div><div class="goal-reader-body" hidden></div><div class="goal-reader-frame" hidden></div>';
      document.getElementById("main").append(reader); cover(); renderReader(); reader.querySelector('[data-goal-act="close-report"]').focus();
    }
    // app.js rebuilds the chat scaffold when the chat identity changes (read-only, execution, round). An open reader
    // of the same goal stays mounted and keeps covering the new task area; navigation elsewhere has already closed it.
    function replaceMain(markup) {
      const main = document.getElementById("main");
      if (!reader) { main.innerHTML = markup; return; }
      for (const node of [...main.childNodes]) if (node !== reader) { covered.delete(node); node.remove(); }
      reader.insertAdjacentHTML("beforebegin", markup); cover();
    }
    function expandReport() {
      if (reportHTML == null) return; closeReader();
      const reports = current().reports;
      rememberPosition(); drawer = null; if (drawerHost) drawerHost.hidden = true;
      openReader({mode:"drawer", goalId:String(goalId), report:reports.find(item => String(item.id) === String(reportId)), reports, html:reportHTML, loading:false, error:""});
    }
    async function readDirect(generation) {
      const target = reading;
      try {
        const response = await fetch(reportPath(options.project, target.goalId, target.report.id), {headers:{Accept:"text/html"}});
        if (!response.ok) throw Error("报告暂时无法读取，请重试。");
        const html = await response.text(); if (generation !== reportGeneration) return; target.html = html;
      } catch (error) { if (generation === reportGeneration) target.error = error.message; }
      finally { if (generation === reportGeneration) { target.loading = false; renderReader(); } }
    }
    // One explicit action: go to the goal, read its detail once, then show the newest current-round report
    // (or the newest historical one, labelled as such). Every await re-checks the generation, so a late
    // detail or HTML response can never open a report after the user switched, closed or chose other materials.
    async function openDelivery(id) {
      const key = String(id); expandedGroups.delete(byId(id)?.leader_session_id);
      // Inside the same goal keep the person whose conversation is open; only cards from a list navigate.
      if (String(goalId) !== key) options.onNavigate(id);
      if (String(goalId) !== key) return;
      closeDrawer(); const generation = ++reportGeneration; deliveryNotice = {goalId:key, error:null}; changed();
      await load(id);
      if (generation !== reportGeneration || String(goalId) !== key) return;
      const reports = errors.has(key) ? [] : details.get(key).reports, report = currentReport(reports, goal()) || reports[0];
      if (!report) { deliveryNotice = {goalId:key, error:errors.has(key) ? errors.get(key) : "这个任务目前没有可阅读的交付报告。"}; changed(); restoreFocus('[data-goal-tools] [data-goal-act="delivery"]'); return; }
      deliveryNotice = null; changed();
      openReader({mode:"direct", goalId:key, report, reports, html:null, loading:true, error:""});
      await readDirect(generation);
    }
    function retryDirect() {
      if (reading?.mode !== "direct" || reading.loading) return;
      const generation = ++reportGeneration; reading.loading = true; reading.error = ""; renderReader(); void readDirect(generation);
    }
    function returnFromReport() {
      if (reading.mode === "direct") { reportGeneration++; closeReader(); changed(); restoreFocus('[data-goal-tools] [data-goal-act="delivery"]'); return; }
      closeReader(); drawer = "reports"; renderDrawer(); changed(); document.querySelector?.('[data-goal-act="expand-report"]')?.focus();
    }
    function showActionConfirm(action) {
      const active = goal(); if (!active) return;
      formOverlay?.remove(); formOverlay = document.createElement("div"); formOverlay.className = "session-config-backdrop";
      const cancelling = action === "cancel";
      options.onOverlay?.();
      formOverlay.innerHTML = `<form class="session-config-dialog goal-create-form" data-goal-confirm="${action}" data-goal-id="${esc(active.id)}" data-version="${active.version}" role="dialog" aria-modal="true" aria-labelledby="goal-confirm-title"><div class="session-config-head"><h2 id="goal-confirm-title">${cancelling ? "取消目标" : "需要修改"}「${esc(active.title)}」${cancelling ? "？" : ""}</h2></div><p class="session-config-description">${cancelling ? "将停止这个目标的执行，保留对话、交付物和相关文档。之后可再开一轮。" : "填写需要修改的具体理由。退回后仍可在本任务中继续聊和修改；原始交付报告与本次验收记录会保留。"}</p>${cancelling ? "" : '<label>修改理由<textarea name="note" required rows="4" placeholder="哪里还不符合验收标准？"></textarea></label>'}<p class="error-line" role="alert"></p><div class="session-config-actions"><button type="button" class="btn ghost" data-goal-act="close-create">返回</button><button type="submit" class="btn primary">${cancelling ? "确认取消这个目标" : "确认需要修改"}</button></div></form>`;
      document.body.append(formOverlay); formOverlay.querySelector(cancelling ? '[data-goal-act="close-create"]' : "textarea").focus();
    }
    async function runAction(active, action, note) {
      if (actionBusy) return false; actionBusy = true; pendingAction = action; actionError = ""; changed();
      try { await options.api("/api/goals/action", {goal_id:active.id,action,version:active.version,...(action === "accept" ? {note:note || ""} : note === undefined ? {} : {note})}); await options.refreshState(); await load(active.id); return true; }
      catch (error) { if (String(goalId) === String(active.id)) actionError = error.message; options.toast(error.message); if (error.status === 409) void load(active.id); return false; }
      finally { actionBusy = false; pendingAction = ""; changed(); }
    }
    function showCreate(id) {
      const person = state().leaders.find(item => item.session_id === id); if (!person) return;
      formOverlay?.remove(); formOverlay = document.createElement("div"); formOverlay.className = "session-config-backdrop";
      options.onOverlay?.();
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
      if (form.hasAttribute("data-goal-confirm")) { event.preventDefault(); if (actionBusy) return; const button = form.querySelector('[type="submit"]'), action = form.dataset.goalConfirm, note = action === "reject" ? form.elements.note.value.trim() : undefined; if (action === "reject" && !note) { form.querySelector(".error-line").textContent = "请填写需要修改的具体理由。"; return; } button.disabled = true;
        const ok = await runAction({id:form.dataset.goalId,version:Number(form.dataset.version)},action,note); if (ok) { formOverlay?.remove(); formOverlay = null; restoreFocus('[data-goal-tools] [data-goal-act="drawer"]'); } else { form.querySelector(".error-line").textContent = actionError; button.disabled = false; } return; }
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
      else if (act === "mode" && !actionBusy) {
        const active=goal();if(!active || active.decision_mode?.mode===button.dataset.mode)return;
        actionBusy=true;changed();
        try{await options.api("/api/goals/agent",{actor_session_id:"user",action:"mode.set",data:{goal_id:active.id,mode:button.dataset.mode,expected_version:active.decision_mode?.version || 0}});await options.refreshState();await load(active.id);options.toast("已保存本任务的决策方式；不会因此启动执行。");}
        catch(error){options.toast(error.message);}
        finally{actionBusy=false;changed();}
      }
      else if (act === "versions") { const active=goal();if(active)void window.CrewGoalVersions.open({api:options.api,goal:active,onChanged:async()=>{await options.refreshState();await load(active.id);}}); }
      else if (act === "edit-stage") { const active=goal(),report=current()?.reports.find(item => item.id===button.dataset.reportId);if(active&&report)void window.CrewStageReport.edit({api:options.api,goal:active,report,onSaved:async()=>{await options.refreshState();await load(active.id);options.toast("阶段说明已保存为新版本。");}}); }
      else if (act === "delivery") void openDelivery(button.dataset.goalId);
      else if (act === "retry-report") retryDirect();
      else if (act === "conclusion-toggle" || act === "report-notes-toggle") { const key = `${goalId}:${button.dataset.reportId}`, expanded = act === "report-notes-toggle" ? reportNotesOpen : conclusionsOpen; if (expanded.has(key)) expanded.delete(key); else expanded.add(key); changed(); }
      else if (act === "general") { expandedGroups.delete(ownerId); options.onGeneral(ownerId); document.querySelector?.('[data-goal-act="general"]')?.focus(); }
      else if (act === "list") { expandedGroups.add(ownerId); closeDrawer(); changed(); document.querySelector?.('[data-goal-act="list"]')?.focus(); }
      else if (act === "create") showCreate(button.dataset.leaderId);
      else if (act === "close-create" && !actionBusy) { const action = formOverlay?.querySelector("[data-goal-confirm]")?.dataset.goalConfirm; formOverlay?.remove(); formOverlay = null; restoreFocus(action ? `[data-action="${action}"]` : '[data-goal-act="create"]'); }
      else if (act === "drawer") { if (drawer === button.dataset.kind) closeDrawer(); else { options.onDrawer?.(); closeReader(); drawer = button.dataset.kind; reportId = null; reportHTML = null; reportGeneration++; deliveryNotice = null; renderDrawer(); drawerHost.querySelector("h2").focus(); if (drawer === "reports") void loadDocuments(); } changed(); }
      else if (act === "close-drawer") { const previous = drawer; closeDrawer(); changed(); restoreFocus(`[data-goal-act="drawer"][data-kind="${previous}"]`); }
      else if (act === "refresh") { if (drawer === "reports") void loadDocuments().then(() => load()); else void load(); }
      else if (act === "report") void openReport(button.dataset.reportId);
      else if (act === "expand-report") expandReport();
      else if (act === "close-report") returnFromReport();
      else if (act === "correct") { const item = current()?.context?.items.find(item => String(item.id) === button.dataset.itemId); if (item) { drafts.set(`${goalId}:${item.id}`, item.text); renderDrawer(); } }
      else if (act === "correct-close") { drafts.delete(`${goalId}:${button.dataset.itemId}`); renderDrawer(); }
      else if (act === "expire" && !actionBusy) void contextAction("expire", button.dataset.itemId);
      else if (act === "action" && !actionBusy) {
        const active = goal(); if (!active) return;
        if (["cancel","reject"].includes(button.dataset.action)) showActionConfirm(button.dataset.action); else await runAction(active,button.dataset.action);
      }
    });
    function escape() {
      if (formOverlay) { if (actionBusy) return true; const action = formOverlay.querySelector("[data-goal-confirm]")?.dataset.goalConfirm; formOverlay.remove(); formOverlay = null; restoreFocus(action ? `[data-action="${action}"]` : '[data-goal-act="create"]'); }
      else if (reader) returnFromReport();
      else if (drawer) { const previous = drawer; closeDrawer(); changed(); restoreFocus(`[data-goal-act="drawer"][data-kind="${previous}"]`); }
      else return false;
      return true;
    }
    document.addEventListener("keydown", event => {
      const tab = event.target.closest?.('.goal-view-tabs [role="tab"]');
      if (tab && ["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) { event.preventDefault(); const act = event.key === "Home" || event.key === "ArrowLeft" ? "general" : "list"; tab.parentElement.querySelector(`[data-goal-act="${act}"]`).click(); document.querySelector(`.goal-view-tabs [data-goal-act="${act}"]`)?.focus(); return; }
      if (event.key !== "Tab" || !formOverlay) return;
      const items = [...formOverlay.querySelectorAll('button:not([disabled]),input,textarea')], first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    return {sync, closeDrawer, escape, showList() { expandedGroups.add(ownerId); closeDrawer(); changed(); }, groupMarkup, sideMarkup, materialsOpen:() => drawer !== null || reading !== null, cards:() => cards(goals(), state()?.leaders), refresh:load, currentGoal:goal, executionFor, showingList:() => expandedGroups.has(ownerId), closeList:() => expandedGroups.delete(ownerId), renderMaterials, replaceMain,
      invalidate() { requestedVersion.clear(); details.clear(); }};
  }
  window.CrewGoals = {create, cards, status, reportPath, reportDocument};
})();
