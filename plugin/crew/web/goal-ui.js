"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const labels = {idle:"空闲", queued:"待规划", running:"推进中", pausing:"暂停中", paused:"已暂停", waiting_user:"等待用户", awaiting_acceptance:"待验收", completed:"已完成", failed:"未达标", cancelled:"已取消"};
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
    return goals.length ? `<div class="goal-rows">${goals.map(goal => {
      const owner = leaders.find(item => item.session_id === goal.leader_session_id), work = goal.work_summary;
      return `<button class="goal-row" data-goal-act="open" data-goal-id="${esc(goal.id)}" aria-pressed="${String(selected) === String(goal.id)}"><span class="goal-status" data-status="${esc(goal.observing ? "observing" : goal.status)}">${goal.observing ? "观察中" : esc(status(goal.status))}</span><strong title="${esc(goal.title)}">${esc(goal.title)}</strong>${work?.total ? `<progress max="${work.total}" value="${work.done}" aria-label="${esc(goal.title)} 工作清单进度"></progress>` : '<span class="goal-row-progress-empty" title="尚无工作清单"></span>'}<span class="goal-row-owner">${esc(owner?.name || "负责人")}</span></button>`;
    }).join("")}</div>` : '<div class="goals-empty">还没有执行目标。需求明确后再建立目标。</div>';
  }
  // A report's relation is recomputed from the newest goal state, so an open report follows later round,
  // report changes. Reports arrive newest first by round and saved report version.
  const isCurrent = (report, goal) => report.round === goal.current_round;
  const currentReport = (reports, goal) => reports.find(item => isCurrent(item, goal)) || null;
  function reportScope(report, goal, reports) {
    if (isCurrent(report, goal)) {
      const newer = reports.find(item => isCurrent(item, goal) && item.version > report.version);
      return newer ? {label:"较早版本", current:false, note:`已有更新版本（第 ${newer.version} 稿）`} : {label:"当前版本", current:true, note:""};
    }
    if (currentReport(reports, goal)) return {label:"历史报告", current:false, note:"已有更新版本"};
    return {label:"历史报告", current:false, note:"当前修改尚无新交付"};
  }
  function supplementsMarkup(items, people, open) {
    if (items.length === 0) return "";
    return `<section class="goal-supplements"><button type="button" class="text-link" data-goal-act="supplements-toggle" aria-expanded="${open}">交付后你又补充了 ${items.length} 条 · ${open ? "收起" : "展开"}</button>${open ? `<ol>${[...items].sort((a,b) => a.version-b.version).map(item => `<li><div><span>需求 v${esc(item.version)}${item.actor === "user" ? "" : ` · ${esc(people.find(person => person.session_id === item.actor).name)}`}</span><time datetime="${esc(item.at)}">${esc(when(item.at))}</time></div><p>${esc(item.text)}</p></li>`).join("")}</ol>` : ""}</section>`;
  }
  const listIcon = () => '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="m7 8 1 1 2-2m-3 6 1 1 2-2m-3 6 1 1 2-2M13 8h4m-4 5h4m-4 5h4"/></svg>';
  const modeIcon = mode => mode === "autonomous" ? '<path d="m13 3-8 10h6l-1 8 9-11h-6z"/>' : '<path d="M8 12V6a2 2 0 0 1 4 0v5-7a2 2 0 0 1 4 0v8-5a2 2 0 0 1 4 0v8c0 4-3 6-7 6h-1c-2 0-3-1-4-3l-4-5a2 2 0 0 1 3-2l1 1z"/>';
  // Running tasks share one play mark in the sidebar list and the task cards; progress lives in the checklist bar.
  const runningIcon = '<circle cx="12" cy="12" r="9" stroke-dasharray="38 18"/><path d="m9 8 6 4-6 4z"/>';
  function leaderTasks(items, selected) {
    return `<div class="leader-tasks">${items.filter(item => !ended(item.status)).map(item => {
      const review = item.status === "awaiting_acceptance", decision = item.pending_decision !== null;
      const quiet = ["paused","pausing","queued","idle"].includes(item.status);
      const state = review ? (item.observing ? "observing" : "review") : decision ? "decision" : quiet ? "quiet" : "running";
      const icon = review ? '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>' : decision ? '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>' : quiet ? '<circle cx="12" cy="12" r="9"/><path d="M9 8v8m6-8v8"/>' : runningIcon;
      const tag = review ? (item.observing ? "观察" : "验收") : decision ? "拍板" : ["paused","pausing"].includes(item.status) ? "暂停" : `${item.work_summary.done}/${item.work_summary.total}`;
      return `<button type="button" class="leader-task" data-goal-act="open" data-goal-id="${esc(item.id)}" data-state="${state}" aria-pressed="${String(selected) === String(item.id)}"><svg class="i" viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span class="leader-task-title" title="${esc(item.title)}">${esc(item.title)}</span><span class="leader-task-tag">${tag}</span></button>`;
    }).join("")}</div>`;
  }
  // The user's own acceptance decision. The same two buttons appear on the workspace home, at the top of the
  // task detail and in the report reader, always bound to the report the user is looking at.
  function reviewButtons(item, reportId, busy = false) {
    const data = `data-goal-act="review" data-goal-id="${esc(item.id)}" data-version="${esc(item.version)}" data-report-id="${esc(reportId || "")}" ${busy ? "disabled" : ""}`;
    return `<span class="review-buttons"><button type="button" class="btn primary review-accept" data-review="accept" ${data}>通过</button><button type="button" class="btn review-reject" data-review="reject" ${data}>需修改</button>${item.observing ? "" : `<button type="button" class="btn ghost review-observe" data-review="observe" title="看过了、目前没问题，先用一段时间再决定；任务保持打开" ${data}>先观察</button>`}</span>`;
  }
  function taskCard(item, people, selected, avatar, ago) {
    const review = item.status === "awaiting_acceptance", decision = !ended(item.status) && item.pending_decision;
    // Assignments are never released, so closed or removed members stay assigned but are no longer listed people.
    const person = id => people.find(entry => entry.session_id === id);
    const work = item.work_summary, doing = item.now_doing?.[0], doer = doing && person(doing.session_id);
    const summary = review ? (item.observing ? `观察中${item.observing.note ? "：" + item.observing.note : "：看过了，先用着再决定"}` : item.acceptance_brief?.summary) : decision ? decision.question : item.status === "running" && doing ? `正在：${doer ? doer.name + " " : ""}${doing.title}` : "";
    const running = !review && !decision && item.status === "running";
    const icon = review ? '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>' : decision ? '<circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/><path d="M12 7v6m0 3v1"/>' : running ? runningIcon : '<circle cx="12" cy="12" r="9"/><path d="M9 8v8m6-8v8"/>';
    const members = item.assignments.map(assignment => person(assignment.session_id)).filter(Boolean);
    return `<button type="button" class="task-card ${review ? (item.observing ? "is-observing" : "is-review") : decision ? "is-decision" : ""}" data-goal-act="open" data-goal-id="${esc(item.id)}" aria-pressed="${String(selected) === String(item.id)}"><span class="task-card-heading"><svg class="i task-state-icon ${running ? "running" : ""}" viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><strong title="${esc(item.title)}">${esc(item.title)}</strong><span class="goal-status" data-status="${esc(item.status)}">${review ? (item.observing ? "观察中" : "待验收") : decision ? "等你拍板" : item.status === "running" && item.finish_pending ? "提交验收中" : item.status === "running" ? "执行中" : esc(status(item.status))}</span></span>${summary ? `<span class="task-card-summary ${decision && !review ? "decision-question" : ""}" title="${esc(summary)}">${esc(summary)}</span>` : ""}${work.total > 0 ? `<span class="task-card-progress"><span>清单 ${work.done}/${work.total}</span><progress max="${work.total}" value="${work.done}" aria-label="${esc(item.title)} 清单进度"></progress></span>` : ""}<span class="task-card-foot"><span class="task-card-people"><span class="task-avatar-stack">${members.slice(0,3).map(person => avatar(person.name,18,true)).join("")}${members.length > 3 ? `<span class="task-avatar-more">+${members.length-3}</span>` : ""}</span><span>${item.working_count} 人在干活</span></span><span class="task-card-mode"><svg class="i" viewBox="0 0 24 24" aria-hidden="true">${modeIcon(item.decision_mode.mode)}</svg>${item.decision_mode.mode === "autonomous" ? "自主推进" : "由我拍板"}</span>${item.current_round > 1 ? `<span>第 ${item.current_round} 轮</span>` : ""}${review && item.supplements_after_report.length > 0 ? `<span class="task-card-supplements">交付后你补充了 ${item.supplements_after_report.length} 条</span>` : ""}<time class="task-card-updated" datetime="${esc(item.last_activity_at)}" title="${esc(when(item.last_activity_at))}">${esc(ago(item.last_activity_at))}</time></span></button>`;
  }
  function create(options) {
    const details = new Map(), loading = new Map(), requestedVersion = new Map(), errors = new Map();
    const endedClosed = new Set(), conclusionsOpen = new Set(), reportNotesOpen = new Set(), supplementsOpen = new Set();
    let pendingAction = "", actionError = "";
    let ownerId = null, goalId = null, reportGeneration = 0, reader = null, formOverlay = null, actionBusy = false;
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
      if (target?.closest?.('#task-list')) options.onReturn?.();
      (document.querySelector?.(selector) || document.querySelector?.('[data-act="tasks-toggle"]'))?.focus();
    }
    function executionFor(id) {
      const active = goal();
      return (current()?.executions || []).filter(item => item.session_id === id && item.round === active?.current_round).sort((a,b) => b.generation - a.generation)[0] || null;
    }
    function closeReader() { options.onReader?.(false); reader?.remove(); reader = null; reading = null; for (const node of covered) node.inert = false; covered.clear(); }
    // The reader covers the task area: nothing behind it may take focus or input. The sidebar stays usable; onReader makes the floating auxiliary panels inert.
    function cover() { for (const node of document.getElementById("main").children) if (node !== reader && !node.inert) { node.inert = true; covered.add(node); } }
    function closeReport() { reportGeneration++; deliveryNotice = null; closeReader(); }
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
        finally { loading.delete(key); if (String(goalId) === key) { changed(); renderReport(); } }
      })();
      loading.set(key, task); return task;
    }
    function sync(nextOwner, nextGoal) {
      if (ownerId !== nextOwner || String(goalId) !== String(nextGoal)) { closeReport(); actionError = ""; ownerId = nextOwner; goalId = nextGoal; }
      const listed = byId(goalId), key = String(goalId);
      if (goalId != null && !loading.has(key) && (!requestedVersion.has(key) || listed && requestedVersion.get(key) !== signature(goalId))) void load();
      renderReport();
    }
    function renderReport() { if (reader) renderReader(); }
    function deliveryMarkup(active, reports) {
      const key = String(active.id);
      const notice = deliveryNotice?.goalId !== key ? "" : deliveryNotice.error == null ? '<p class="goal-delivery-status note" role="status">正在打开交付报告…</p>'
        : `<p class="goal-action-error error-line" role="alert">交付报告未能打开：${esc(deliveryNotice.error)} <button class="text-link" data-goal-act="delivery" data-goal-id="${esc(key)}">重试</button></p>`;
      if (!reports.length) return notice;
      const report = currentReport(reports, active);
      if (!report) return `<div class="goal-delivery goal-delivery-gap"><span>${esc(reportScope(reports[0], active, reports).note)}。</span><button class="text-link" data-goal-act="delivery" data-goal-id="${esc(key)}">查看历史版本</button></div>${notice}`;
      const open = conclusionsOpen.has(`${key}:${report.id}`), counts = report.acceptance_counts, unresolved = report.unresolved;
      const recorded = acceptanceLabels.filter(([name]) => counts[name] > 0);
      const total = recorded.reduce((sum, [name]) => sum + counts[name], 0);
      const notesOpen = reportNotesOpen.has(`${key}:${report.id}`);
      const folds = report.summary.length > 40 || report.summary.includes("\n");
      const notes = unresolved.length ? `<div class="goal-delivery-notes"><button class="text-link" data-goal-act="report-notes-toggle" data-report-id="${esc(report.id)}" aria-expanded="${notesOpen}">尚存问题 · ${unresolved.length} 条 · ${notesOpen ? "收起" : "展开"}</button>${notesOpen ? `<p>报告记录的限制与未完成事项：</p><ul class="goal-delivery-unresolved-list">${unresolved.map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</div>` : "";
      return `<section class="goal-delivery" aria-label="当前交付结论"><div class="goal-delivery-head"><strong>本次结果</strong><span>${esc(outcomes[report.status])}</span></div><p class="goal-delivery-summary ${open ? "open" : ""}">${esc(report.summary)}</p>${active.status === "awaiting_acceptance" ? supplementsMarkup(active.supplements_after_report, [...state().leaders, ...state().members, ...(state().git_assistant === null ? [] : [state().git_assistant])], supplementsOpen.has(key)) : ""}<div class="goal-delivery-foot"><div class="goal-delivery-meta"><span>AI 验证</span>${recorded.length ? `<span class="goal-acceptance-total">共 ${esc(total)} 项</span>${recorded.map(([name,label]) => `<span class="goal-acceptance-count" data-kind="${name}">${label} ${esc(counts[name])}</span>`).join("")}` : "<span>没有逐项验收记录</span>"}</div><div class="goal-delivery-actions">${folds ? `<button class="text-link" data-goal-act="conclusion-toggle" data-report-id="${esc(report.id)}" aria-expanded="${open}">${open ? "收起" : "展开全文"}</button>` : ""}<button class="btn ghost" data-goal-act="delivery" data-goal-id="${esc(key)}">打开报告</button></div></div>${notes}</section>${notice}`;
    }
    function modeMarkup() {
      const active = goal();
      return active ? `<div class="goal-mode-control" role="group" aria-label="本任务决策方式">${[["human","由我拍板"],["autonomous","自主推进"]].map(([mode,label]) => `<button type="button" data-goal-act="mode" data-mode="${mode}" aria-pressed="${active.decision_mode?.mode === mode}" ${actionBusy ? "disabled" : ""}>${label}</button>`).join("")}</div>` : "";
    }
    function listMarkup(id) {
      const list = goals().filter(item => item.leader_session_id === id), people = [...state().leaders, ...state().members];
      const review = list.filter(item => item.status === "awaiting_acceptance" && !item.observing), observing = list.filter(item => item.status === "awaiting_acceptance" && item.observing);
      const active = list.filter(item => !ended(item.status) && item.status !== "awaiting_acceptance"), done = list.filter(item => ended(item.status));
      const create = `<button type="button" class="btn ghost" data-goal-act="create" data-leader-id="${esc(id)}">＋ 建立目标</button>`;
      const group = (title, items) => items.length ? `<section class="task-list-group"><h3>${title}<span>${items.length}</span></h3>${items.map(item => taskCard(item, people, goalId, options.avatar, options.ago)).join("")}</section>` : "";
      return `<header class="task-list-head">${listIcon()}<h2>任务<span>${review.length + observing.length + active.length} 项进行中</span></h2>${create}</header><div class="task-list-scroll">${group("待你验收",review)}${group("观察中",observing)}${group("进行中",active)}${done.length ? `<section class="task-list-group task-list-ended"><button type="button" class="task-ended-toggle" data-goal-act="ended-toggle" aria-expanded="${!endedClosed.has(id)}" aria-controls="task-ended-items">${!endedClosed.has(id) ? "⌄" : "›"} 已结束 ${done.length}</button><div id="task-ended-items" ${!endedClosed.has(id) ? "" : "hidden"}>${done.map(item => taskCard(item, people, goalId, options.avatar, options.ago)).join("")}</div></section>` : ""}${list.length === 0 ? `<div class="goals-empty">还没有任务，明确需求后建立一个目标。${create}</div>` : ""}</div>`;
    }
    function detailMarkup() {
      if (goalId == null) return "";
      const active = goal();
      if (!active) return `<p class="note">${esc(errors.get(String(goalId)) || "正在读取任务…")}</p><button class="btn ghost" data-goal-act="refresh">重新读取</button>`;
      const result = current(), reports = result?.reports || [], review = result?.acceptance_review || result?.previous_acceptance_review;
      const followup = '<p class="goal-followup-hint">在总会话输入框旁点亮任务小框，可补充本任务的要求。</p>';
      const actions = active.status === "awaiting_acceptance" ? [] : active.status === "queued" ? [["start", "开始推进"]] : ["paused","waiting_user"].includes(active.status) ? [["resume", "继续推进"]] : !ended(active.status) && active.status !== "pausing" ? [["pause", "暂停目标"]] : [];
      if (!ended(active.status) && active.status !== "awaiting_acceptance") actions.push(["cancel", "取消目标"]);
      const actionButtons = `<div class="goal-actions">${actions.map(([action,label]) => `<button class="btn ${["start","resume","accept"].includes(action) ? "goal-primary-action" : "ghost"}" data-goal-act="action" data-action="${action}" ${actionBusy ? "disabled" : ""}>${pendingAction === action ? "正在处理…" : label}</button>`).join("")}</div>`;
      const people = [...state().leaders, ...state().members];
      const testSteps = active.acceptance_brief?.user_test;
      const reviewing = active.status === "awaiting_acceptance", reportId = active.acceptance_brief?.report_id;
      const watching = reviewing && active.observing;
      const banner = reviewing ? `<section class="task-review-banner${watching ? " is-observing" : ""}" aria-label="${watching ? "观察中" : "等你验收"}"><div class="task-review-text"><strong>${watching ? "观察中" : "等你验收"}</strong><span>${watching ? `你 ${esc(options.ago(active.observing.created_at))}标记了先观察${active.observing.note ? "：" + esc(active.observing.note) : "，先用着再决定"}。任务保持打开，不会叫醒 AI；确认没问题点通过，发现问题点需修改。` : `${esc(owner()?.name || "leader")}已提交交付。看完报告、按“怎么实测”试一遍：没问题就通过，有问题点需修改；想先用一段时间再定，点先观察。`}</span></div><div class="task-review-actions">${reviewButtons(active, reportId, actionBusy)}${reportId ? `<button type="button" class="btn ghost" data-goal-act="delivery" data-goal-id="${esc(active.id)}">看完整报告</button>` : ""}</div></section>`
        : active.status === "running" && active.finish_pending ? '<section class="task-review-banner is-pending" role="status"><div class="task-review-text"><strong>提交验收中</strong><span>leader 已提交交付报告，本轮执行结束后自动进入待验收。</span></div></section>' : "";
      const userTest = active.status === "awaiting_acceptance" && testSteps?.length ? `<div class="task-user-test"><h3>怎么实测</h3><ol>${testSteps.map(item => `<li><p>${esc(item.step)}</p><p>预期：${esc(item.expected)}</p></li>`).join("")}</ol></div>` : "";
      return `<header class="task-detail-head"><div class="goal-detail-title"><button type="button" class="icon-btn" data-act="tasks-back" aria-label="返回任务清单"><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg></button><h1 title="${esc(active.title)}">${esc(active.title)}</h1><span class="goal-status" data-status="${esc(active.observing ? "observing" : active.status)}">${active.observing ? "观察中" : esc(status(active.status))}</span></div><div class="task-detail-controls">${modeMarkup()}${actions.length ? actionButtons : ""}</div></header><div class="task-detail-scroll">${banner}<section class="goal-tools" data-goal-tools="${esc(active.id)}"><p class="task-summary">${active.decision_mode.mode === "autonomous" ? "自主推进" : "由我拍板"}${active.current_round > 1 ? ` · 第 ${active.current_round} 轮` : ""} · ${active.working_count} 人在干活 · <time datetime="${esc(active.last_activity_at)}" title="${esc(when(active.last_activity_at))}">${esc(options.ago(active.last_activity_at))}</time></p><section class="task-delivery" tabindex="-1" aria-label="交付结果"><h2>交付结果</h2>${result == null && !errors.has(String(active.id)) ? '<p class="note">正在读取交付…</p>' : reports.length ? deliveryMarkup(active, reports) : '<p class="note">还没有交付</p>' + deliveryMarkup(active, reports)}${userTest}${options.renderDocuments?.(result?.documents || [], goalId) || ""}${review ? `<details class="goal-review"><summary>${review.round < active.current_round ? "之前记录 · " : ""}${review.accepted ? "你已验收通过" : "你退回修改"}${review.note ? `：${esc(review.note.slice(0,80))}${review.note.length > 80 ? "…" : ""}` : ""}</summary><p>${esc(review.note || "你确认交付符合验收要求。")}<br><span>原始交付报告保持生成时的内容和状态。</span></p></details>` : ""}${actionError || errors.has(String(goalId)) && deliveryNotice?.goalId !== String(goalId) ? `<p class="goal-action-error error-line" role="alert">${esc(actionError || errors.get(String(goalId)))}</p>` : ""}</section><section class="task-progress"><h2>进度</h2>${window.CrewGoalWork.markup(result?.work_items || [], people)}<ul class="task-owners">${(result?.executions || []).filter(item => item.round === active.current_round).map(item => { const person = people.find(person => person.session_id === item.session_id); return `<li>${esc(person ? person.name : item.session_id + "（已离开）")} · ${esc(status(item.status))}</li>`; }).join("")}</ul></section><section class="task-requirements"><h2>需求</h2>${requirementsMarkup(active)}</section>${followup}</section></div>`;
    }

    function requirementsMarkup(active) {
      return `<section class="goal-requirements"><h3>${esc(active.title)}</h3><strong>原始需求与验收标准</strong><p>${esc(active.request)}</p>${active.acceptance?.length ? `<ol>${active.acceptance.map(item => `<li>${esc(typeof item === "string" ? item : item.title)}</li>`).join("")}</ol>` : '<p class="note">尚未写明验收标准</p>'}${active.constraints?.length ? `<strong>约束</strong><ul>${active.constraints.map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</section>`;
    }
    function readerHeadMarkup() {
      const active = goal(), report = reading.report, scope = reportScope(report, active, reading.reports);
      return `<div class="goal-reader-heading"><div class="title">${esc(active.title)} · 交付报告</div><div class="sub goal-reader-meta"><span class="goal-report-scope" data-current="${scope.current}">${scope.label}</span><span>需求 v${esc(report.requirement_version)} · 第 ${esc(report.version)} 稿</span><span>AI 报告结果：${esc(outcomes[report.status])}</span>${scope.note ? `<span class="goal-reader-gap">${esc(scope.note)}</span>` : ""}<span>${esc(owner()?.name)}组 · 隔离预览</span></div></div>${report.status === "progress" && scope.current && !["completed","cancelled","awaiting_acceptance"].includes(active.status) ? `<button class="btn ghost" data-goal-act="edit-stage" data-report-id="${esc(report.id)}">编辑阶段说明</button>` : ""}${active.status === "awaiting_acceptance" && report.id === active.acceptance_brief?.report_id ? reviewButtons(active, report.id, actionBusy) : ""}<button class="btn" data-goal-act="close-report">返回任务</button>`;
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
      const key = String(id);
      // Inside the same goal keep the person whose conversation is open; only cards from a list navigate.
      if (String(goalId) !== key) options.onNavigate(id);
      if (String(goalId) !== key) return;
      closeReport(); const generation = ++reportGeneration; deliveryNotice = {goalId:key, error:null}; changed();
      await load(id);
      if (generation !== reportGeneration || String(goalId) !== key) return;
      const reports = errors.has(key) ? [] : details.get(key).reports, report = currentReport(reports, goal()) || reports[0];
      if (!report) { deliveryNotice = {goalId:key, error:errors.has(key) ? errors.get(key) : "这个任务目前没有可阅读的交付报告。"}; changed(); restoreFocus('[data-goal-tools] [data-goal-act="delivery"]'); return; }
      deliveryNotice = null; changed();
      openReader({goalId:key, report, reports, html:null, loading:true, error:""});
      await readDirect(generation);
    }
    function retryDirect() {
      if (!reading || reading.loading) return;
      const generation = ++reportGeneration; reading.loading = true; reading.error = ""; renderReader(); void readDirect(generation);
    }
    function returnFromReport() { closeReport(); changed(); restoreFocus('[data-goal-tools] [data-goal-act="delivery"]'); }
    function showActionConfirm(action, active = goal()) {
      if (!active) return;
      formOverlay?.remove(); formOverlay = document.createElement("div"); formOverlay.className = "session-config-backdrop";
      const cancelling = action === "cancel";
      options.onOverlay?.();
      formOverlay.innerHTML = `<form class="session-config-dialog goal-create-form" data-goal-confirm="${action}" data-goal-id="${esc(active.id)}" data-version="${active.version}" data-report-id="${esc(active.report_id || "")}" role="dialog" aria-modal="true" aria-labelledby="goal-confirm-title"><div class="session-config-head"><h2 id="goal-confirm-title">${cancelling ? "取消目标" : "需要修改"}「${esc(active.title)}」${cancelling ? "？" : ""}</h2></div><p class="session-config-description">${cancelling ? "将停止这个目标的执行，保留对话、交付物和相关文档。相关记录仍可查看。" : "写清哪里还不符合要求。提交后 leader 会直接在本任务里按意见修改，改完重新提交验收；原始交付报告与这次验收记录都会保留。"}</p>${cancelling ? "" : '<label>修改理由<textarea name="note" required rows="4" placeholder="哪里还不符合验收标准？"></textarea></label>'}<p class="error-line" role="alert"></p><div class="session-config-actions"><button type="button" class="btn ghost" data-goal-act="close-create">返回</button><button type="submit" class="btn primary">${cancelling ? "确认取消这个目标" : "提交修改意见"}</button></div></form>`;
      document.body.append(formOverlay); formOverlay.querySelector(cancelling ? '[data-goal-act="close-create"]' : "textarea").focus();
    }
    async function runAction(active, action, note) {
      if (actionBusy) return false; actionBusy = true; pendingAction = action; actionError = ""; changed();
      try {
        const result = await options.api("/api/goals/action", {goal_id:active.id,action,version:active.version,...(active.report_id ? {report_id:active.report_id} : {}),...(action === "accept" ? {note:note || ""} : note === undefined ? {} : {note})});
        if (action === "accept") options.toast("已验收通过，任务完成。");
        else if (action === "observe") options.toast("已标记观察中：任务保持打开，随时可以通过或需修改。");
        else if (action === "reject") options.toast(result.resumed === false ? `修改意见已保存；没能直接续上任务（${result.resume_error}），已通知 leader。` : "修改意见已交给 leader，在本任务里继续修改。");
        if (["accept","reject","observe"].includes(action) && reading?.goalId === String(active.id)) closeReport();
        await options.refreshState(); await load(active.id); return true;
      }
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
    document.addEventListener("submit", async event => {
      const form = event.target;
      if (form.hasAttribute("data-goal-confirm")) { event.preventDefault(); if (actionBusy) return; const button = form.querySelector('[type="submit"]'), action = form.dataset.goalConfirm, note = action === "reject" ? form.elements.note.value.trim() : undefined; if (action === "reject" && !note) { form.querySelector(".error-line").textContent = "请填写需要修改的具体理由。"; return; } button.disabled = true;
        const ok = await runAction({id:form.dataset.goalId,version:Number(form.dataset.version),report_id:form.dataset.reportId || null},action,note); if (ok) { formOverlay?.remove(); formOverlay = null; restoreFocus('[data-goal-tools] [data-goal-act="delivery"]'); } else { form.querySelector(".error-line").textContent = actionError; button.disabled = false; } return; }
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
      if (act === "open") options.onNavigate(button.dataset.goalId);
      else if (act === "mode" && !actionBusy) {
        const active=goal();if(!active || active.decision_mode?.mode===button.dataset.mode)return;
        actionBusy=true;changed();
        try{await options.api("/api/goals/agent",{actor_session_id:"user",action:"mode.set",data:{goal_id:active.id,mode:button.dataset.mode,expected_version:active.decision_mode?.version || 0}});await options.refreshState();await load(active.id);options.toast("已保存本任务的决策方式；不会因此启动执行。");}
        catch(error){options.toast(error.message);}
        finally{actionBusy=false;changed();}
      }
      else if (act === "edit-stage") { const active=goal(),report=current()?.reports.find(item => item.id===button.dataset.reportId);if(active&&report)void window.CrewStageReport.edit({api:options.api,goal:active,report,onSaved:async()=>{await options.refreshState();await load(active.id);options.toast("阶段说明已保存为新版本。");}}); }
      else if (act === "supplements-toggle") { const home = button.closest("[data-home-supplements]"); if (home) { window.CrewWorkspaceHome.toggleSupplements(home.dataset.homeSupplements); changed(); return; } if (supplementsOpen.has(String(goalId))) supplementsOpen.delete(String(goalId)); else supplementsOpen.add(String(goalId)); changed(); }
      else if (act === "delivery") void openDelivery(button.dataset.goalId);
      else if (act === "retry-report") retryDirect();
      else if (act === "conclusion-toggle" || act === "report-notes-toggle") { const key = `${goalId}:${button.dataset.reportId}`, expanded = act === "report-notes-toggle" ? reportNotesOpen : conclusionsOpen; if (expanded.has(key)) expanded.delete(key); else expanded.add(key); changed(); }
      else if (act === "ended-toggle") { if (endedClosed.has(ownerId)) endedClosed.delete(ownerId); else endedClosed.add(ownerId); changed(); }
      else if (act === "create") showCreate(button.dataset.leaderId);
      else if (act === "close-create" && !actionBusy) { const action = formOverlay?.querySelector("[data-goal-confirm]")?.dataset.goalConfirm; formOverlay?.remove(); formOverlay = null; restoreFocus(action ? `[data-action="${action}"],[data-review="${action}"]` : '[data-goal-act="create"]'); }
      else if (act === "refresh") void load();
      else if (act === "close-report") returnFromReport();
      else if (act === "review" && !actionBusy) {
        const listed = byId(button.dataset.goalId);
        const target = {id:button.dataset.goalId, version:Number(button.dataset.version), report_id:button.dataset.reportId || null, title:listed?.title || ""};
        if (button.dataset.review === "reject") showActionConfirm("reject", target); else await runAction(target, button.dataset.review === "observe" ? "observe" : "accept");
      }
      else if (act === "action" && !actionBusy) {
        const active = goal(); if (!active) return;
        if (["cancel","reject"].includes(button.dataset.action)) showActionConfirm(button.dataset.action); else await runAction(active,button.dataset.action);
      }
    });
    function escape() {
      if (formOverlay) { if (actionBusy) return true; const action = formOverlay.querySelector("[data-goal-confirm]")?.dataset.goalConfirm; formOverlay.remove(); formOverlay = null; restoreFocus(action ? `[data-action="${action}"],[data-review="${action}"]` : '[data-goal-act="create"]'); }
      else if (reader) returnFromReport();
      else return false;
      return true;
    }
    document.addEventListener("keydown", event => {
      if (event.key !== "Tab" || !formOverlay) return;
      const items = [...formOverlay.querySelectorAll('button:not([disabled]),input,textarea')], first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    return {sync, closeReport, escape, listMarkup, detailMarkup, cards:() => cards(goals(), state()?.leaders), refresh:load, currentGoal:goal, executionFor, replaceMain,
      invalidate() { requestedVersion.clear(); details.clear(); }};
  }
  window.CrewGoals = {create, cards, taskCard, reviewButtons, leaderTasks, listIcon, modeIcon, ended, status, reportPath, reportDocument, supplementsMarkup};
})();
