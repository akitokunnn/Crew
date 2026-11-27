"use strict";
(() => {
  const K = window.CrewChannelKit, P = window.CrewWarRoomPlan;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const ending = {completed:"做完了", user_stop:"你叫停了", abandoned:"领头放弃了",
    budget:"预算用完了", cancelled:"筹备已撤销", legacy:"已退出旧房"};
  const purposes = {creative:"创意", routine:"常规", difficult:"疑难"};
  const itemStatus = {todo:"待做", doing:"在做", blocked:"卡住", done:"完成"};
  const deliveryStatus = {queued:"待送达", undelivered:"未送达", delivered:"已送达", replied:"已回复"};
  const deliveryWait = {busy:"领头还在忙", stopped:"领头已停止", external:"领头在外部窗口", maintenance:"领头正在维护",
    liveness_unknown:"还不清楚领头是否在线", failed:"上次没送成"};
  const launchStep = {leader:"建好领队", invite:"邀请成员", goal:"建好任务", announce:"发布开打公告"};
  const roomPath = "/api/op/war_room.";
  let active = null;
  const current = ctx => active === ctx;
  const action = (name, label, extra = "") => `<button type="button" class="cx-btn" data-wr="${name}" ${extra}>${label}</button>`;
  const roomLink = id => `#war-room=${id}`;
  const narrow = () => window.matchMedia("(max-width:760px)").matches;
  const count = n => n.toLocaleString("zh-CN");
  const atBottom = node => node.scrollHeight - node.clientHeight - node.scrollTop < 48;
  const isOpen = room => room.status === "open";
  const preparing = room => room.phase === "preparing";
  const showsPlan = room => isOpen(room) && preparing(room) && !room.approved_version;
  const latest = room => room.plans.find(plan => plan.version === room.draft_version);
  const selectedPlan = ctx => ctx.planVersion === null ? latest(ctx.room) : ctx.room.plans.find(plan => plan.version === ctx.planVersion);
  const previousPlan = (ctx, plan) => ctx.room.plans.find(item => item.version === plan.version - 1);
  const planRoot = ctx => ctx.panelDialog.open ? ctx.panelContent : ctx.aside;
  const pending = room => room.decisions.filter(item => item.status === "open");
  const assignments = room => room.board.filter(item => item.kind === "assignment" || (room.legacy && item.entry_id == null));
  const jobExecutions = (room, goalId) => room.executions.filter(item => item.goal_id === goalId)
    .sort((a, b) => b.round - a.round || b.generation - a.generation);
  const participants = room => room.members.some(item => item.session_id === room.lead_session_id) ? room.members :
    [{session_id:room.lead_session_id, crew_name:room.lead_name}, ...room.members];
  const canApprove = room => isOpen(room) && !room.legacy && room.draft_version > (room.approved_version ?? 0);
  const field = (label, control) => `<label class="wr-field"><span>${label}</span>${control}</label>`;

  function put(node, html) {
    if (node.crewHTML !== html) {
      const open = new Set([...node.querySelectorAll("details[open][data-wr-card]")].map(card => card.dataset.wrCard));
      node.innerHTML = html;
      node.crewHTML = html;
      for (const card of node.querySelectorAll("details[data-wr-card]")) card.open = open.has(card.dataset.wrCard);
    }
  }

  function roomListHTML(ctx) {
    if (ctx.roomsError) return `<p role="alert">${esc(ctx.roomsError)}</p>${action("reload", "重试")}`;
    if (ctx.rooms === null) return '<p role="status">正在读取作战室…</p>';
    const live = ctx.rooms.filter(isOpen), history = ctx.rooms.filter(room => !isOpen(room));
    const rows = (rooms, label, empty) => `<section class="wr-room-group"><h2>${label}</h2>${rooms.length ? rooms.map(room =>
      `<a class="wr-room-row" href="${roomLink(room.id)}" data-wr-route="${room.id}"><span><strong>${esc(room.title)}</strong><small>${esc(room.goal)} · 领头 ${esc(room.lead_name)}</small></span><span class="wr-room-state" data-state="${CrewGlobalRail.roomState([room])}">${esc(isOpen(room) ? CrewGlobalRail.roomLabel([room]) : ending[room.close_reason])}${room.unread ? ` · ${room.unread} 条未读` : ""}</span></a>`).join("") : `<p>${empty}</p>`}</section>`;
    return `<div class="wr-directory-head"><h1>作战室</h1>${live.length ? "" : ctx.state.leaders.length ? action("open", "开作战室") :
      `<a class="cx-btn" href="/assistant.html?project=${encodeURIComponent(ctx.state.project.name)}#opening=${encodeURIComponent(ctx.state.project.name)}">找助手开局</a>`}</div>` +
      rows(live, "进行中", "当前没有作战室") + rows(history, "历史", "还没有结束的作战室");
  }

  function messageHTML(ctx, message, internal = false) {
    const user = message.speaker_session_id === "user";
    const name = esc(message.speaker_name);
    const avatar = user ? `<span class="av round cx-av-user" style="--s:28px" aria-hidden="true">${name.slice(0, 1)}</span>` : ctx.avatar(message.speaker_name, 28, true);
    const reply = !user && !internal && isOpen(ctx.room) ? action("reply", "回复", `data-message="${message.id}"`) : "";
    const quote = message.reply ? `<blockquote class="wr-message-quote"><strong>${esc(message.reply.speaker_name)}</strong> ${esc(K.plain(message.reply.excerpt))}</blockquote>` : "";
    const mention = user && message.mentions.find(item => item.session_id === ctx.room.lead_session_id);
    const status = mention?.delivery === "queued" && !isOpen(ctx.room) ? "undelivered" : mention?.delivery;
    const waiting = status === "queued" && (deliveryWait[mention.waiting] ?? mention.waiting);
    const delivery = deliveryStatus[status] ? `<small class="wr-delivery" data-delivery="${status}">${deliveryStatus[status]}${waiting ? ` · ${esc(waiting)}` : ""}</small>` : "";
    return `<article class="cx-msg${user ? " cx-user" : ""}">${avatar}<div class="cx-body-col"><div class="cx-meta"><strong>${name}</strong><time datetime="${esc(message.created_at)}">${esc(K.clock(message.created_at))}</time>${reply}</div>${quote}<div class="${user ? "cx-bubble" : "cx-content"}"><div class="markdown cx-md">${CrewChat.renderMarkdown(message.body, message.speaker_session_id)}</div></div>${delivery}</div></article>`;
  }

  function conversation(room) {
    const main = [], internal = [];
    for (const message of room.messages) {
      const lead = message.speaker_session_id === room.lead_session_id;
      const user = message.speaker_session_id === "user";
      (lead || user ? main : internal).push(message);
    }
    return {main, internal};
  }

  function planData(ctx, plan) {
    if (!ctx.documents.has(plan.version)) ctx.documents.set(plan.version, P.document(plan));
    return ctx.documents.get(plan.version);
  }

  function planChanges(ctx, plan) {
    const previous = previousPlan(ctx, plan);
    const {changes} = P.compare(previous ? planData(ctx, previous) : {blocks:[]}, planData(ctx, plan));
    const before = previous?.budget_tokens ?? null;
    if (before !== plan.budget_tokens) changes.push({key:"budget", label:"预算上限", type:before === null ? "新" : "改"});
    return changes;
  }

  function versionCard(ctx, plan) {
    const initial = plan.version === 1, changes = initial ? [] : planChanges(ctx, plan);
    const expanded = ctx.expandedVersions.has(plan.version);
    const chips = changes.slice(0, expanded ? changes.length : 6).map(change =>
      `<li>${action("jump", `<span>${change.type}</span> ${esc(change.label)}`, `data-version="${plan.version}" data-block="${esc(change.key ?? change.heading)}"`)}</li>`).join("");
    const more = changes.length > 6 ? `<li>${action("revision-more", expanded ? "收起" : `+${changes.length - 6} 项`,
      `data-version="${plan.version}" aria-expanded="${expanded}"`)}</li>` : "";
    return `<section class="wr-note wr-revision"><div><strong>${initial ? "计划书 v1" : `计划书 v${plan.version - 1} → v${plan.version}`}</strong><p>${esc(plan.summary)}</p></div>
      ${chips ? `<ul>${chips}${more}</ul>` : action("jump", "看这一版", `data-version="${plan.version}"`)}</section>`;
  }

  function consultHTML(card) {
    const usage = card.usage_status === "pending" ? "用量待确认" : `${count(card.tokens)} tokens`;
    const runtime = ({claude:"Claude Code", codex:"Codex"})[card.runtime] ?? card.runtime;
    const response = card.error ? `<p class="wr-error">${esc(card.error)}</p>` : card.response === null ?
      "<p>正在等答复</p>" : `<div class="crew-md-doc">${CrewReportMarkdown.render(card.response)}</div>`;
    const details = [runtime, card.model ?? "默认模型", `强度 ${card.effort}`, card.native_model && card.native_model !== card.model && `实际 ${card.native_model}`].filter(Boolean).join(" · ");
    return `<details class="wr-note wr-consult" data-wr-card="consult-${esc(card.question_id)}"><summary><strong>${purposes[card.purpose]}顾问</strong><span>${esc(usage)}</span><small title="${esc(details)}">${esc(details)}</small></summary>
      <div class="wr-consult-body"><h3>问题</h3><div class="crew-md-doc">${CrewReportMarkdown.render(card.question)}</div><h3>${card.error ? "顾问调用失败" : "答复"}</h3>${response}</div></details>`;
  }

  function decisionHTML(room, decision) {
    const state = decision.status === "open" && !isOpen(room) ? "closed" : decision.status;
    const label = {open:"等你定", closed:"随房间结束关闭", answered:"已定", withdrawn:"已撤回"}[state];
    return `<section class="wr-note wr-decision" data-state="${state}" id="wr-decision-${decision.id}"><strong>${label}</strong><p>${esc(decision.question)}</p>${decision.answer ? `<p>${esc(decision.answer)}</p>` : ""}
      ${state === "open" ? `${decision.recommendation ? `<p>建议 ${esc(decision.recommendation)}</p>` : ""}<a class="cx-btn" data-act="view" data-view="all" href="#all">去拍板</a>` : ""}</section>`;
  }

  function timelineHTML(ctx, main) {
    const room = ctx.room;
    const rows = main.map(message => ({at:message.created_at, render:() => messageHTML(ctx, message)}));
    for (const plan of [...room.plans].sort((a, b) => a.version - b.version)) rows.push({at:plan.created_at, render:() => versionCard(ctx, plan)});
    for (const card of room.consults) rows.push({at:card.created_at, render:() => consultHTML(card)});
    for (const event of room.events) {
      if (event.kind === "approved") rows.push({at:event.at, render:() => `<p class="wr-note">计划 v${event.detail.version} 已批准，开始作战。</p>`});
    }
    for (const decision of room.decisions) rows.push({at:decision.created_at, render:() => decisionHTML(room, decision)});
    const unread = main.find(message => message.id > ctx.readFrom && message.speaker_session_id !== "user");
    if (unread && isOpen(room)) rows.push({at:unread.created_at, first:true, render:() => '<div class="cx-divider cx-unread"><span>新发言</span></div>'});
    rows.sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || Number(Boolean(b.first)) - Number(Boolean(a.first)));
    return `${room.origin ? `<p class="wr-note"><button type="button" class="text-link" data-wr="origin">查看交流区原帖 #${room.origin.id}</button></p>` : ""}${room.has_more ? '<p class="wr-muted">只显示最近 200 条房内发言</p>' : ""}${rows.map(row => row.render()).join("") || '<p class="cx-empty">还没有发言</p>'}`;
  }

  function planBudget(plan, previous) {
    if (plan.budget_tokens !== null) return `预算上限 ${count(plan.budget_tokens)} tokens`;
    return previous && previous.budget_tokens !== null ? "预算上限已取消" : "";
  }

  function planHTML(ctx, inDialog = false) {
    const room = ctx.room;
    const plan = selectedPlan(ctx);
    if (!plan) return `${inDialog ? "" : '<header class="wr-panel-head"><h2>作战计划书</h2></header>'}<p class="wr-panel-empty">等领头写第一版计划。</p>`;
    const currentPlan = plan.version === room.draft_version, changes = planChanges(ctx, plan);
    const draft = canApprove(room) && currentPlan;
    const questions = draft ? P.pending(plan) : [];
    const decided = plan.version === room.approved_version
      ? room.board.filter(item => item.kind === "decision" && item.status === "done")
      : P.decisions(plan).filter(item => item.answer);
    const closedDecisions = [...decided, ...room.decisions.filter(item => item.status === "answered").map(item => ({title:item.question, answer:item.answer}))];
    const previous = previousPlan(ctx, plan), budget = planBudget(plan, previous);
    const tools = (previous ? action("compare", ctx.compare ? "收起对比" : "对比上一版", `aria-pressed="${ctx.compare}"`) : "") +
      (!currentPlan ? action("latest", "回最新版") : "");
    const heading = inDialog ? "" : `<h2>作战计划书 <span>v${plan.version}</span></h2>`;
    return `${heading || tools ? `<header class="wr-panel-head">${heading}${tools ? `<div>${tools}</div>` : ""}</header>` : ""}
      <div class="wr-panel-scroll">
        ${questions.length ? `<section class="wr-plan-decisions"><h3>等你定 <span>${questions.length}</span></h3>${questions.map(q => `<div class="wr-question"><strong>${esc(q.entry_id)}</strong><div>${esc(q.title)}</div><small>${q.recommendation ? `领头建议 ${esc(q.recommendation)}` : "还没有推荐值"}</small>${action("reference", "引用回答", `data-entry="${esc(q.entry_id)}"`)}</div>`).join("")}</section>` : ""}
        ${closedDecisions.length ? `<details class="wr-plan-decided" data-wr-card="decided"><summary>✓ 已定 (${closedDecisions.length})</summary><ul>${closedDecisions.map(item => `<li>${esc(item.title)}${item.answer ? ` · ${esc(item.answer)}` : ""}</li>`).join("")}</ul></details>` : ""}
        ${ctx.compare && previous ? `<section class="wr-compare"><strong>v${previous.version} → v${plan.version}</strong><ul>${changes.map(change => `<li>${change.type} · ${esc(change.label)}</li>`).join("") || "<li>正文没有改动</li>"}</ul><details data-wr-card="previous-${previous.version}"><summary>展开上一版全文</summary><article class="crew-md-doc">${planData(ctx, previous).html}</article></details></section>` : ""}
        <article class="crew-md-doc wr-plan-document">${budget ? `<p class="wr-plan-budget" data-wr-block="budget">${budget}</p>` : ""}${planData(ctx, plan).html}</article></div>
      ${draft ? `<footer class="wr-plan-action"><p class="wr-error" role="alert">${esc(ctx.approvalError)}</p>${action("approve", preparing(room) ? "敲定，开打" : "敲定修订", `data-version="${plan.version}"`)}</footer>` : ""}`;
  }

  function statusHTML(ctx, internal, inDialog = false) {
    const room = ctx.room, jobs = assignments(room), done = jobs.filter(item => item.status === "done").length;
    const blocked = room.board.filter(item => item.status === "blocked"), decisions = pending(room);
    const steps = room.events.filter(event => event.kind === "launch_step" && event.detail.version === room.approved_version);
    return `${inDialog ? "" : '<header class="wr-panel-head"><h2>战况</h2></header>'}<div class="wr-panel-scroll wr-status-scroll">
      ${isOpen(room) && (blocked.length || decisions.length) ? `<section class="wr-attention"><h3>需要你关注</h3><ul>
      ${blocked.map(item => `<li>卡住了 · ${esc(item.title)}${item.note ? ` · ${esc(item.note)}` : ""}</li>`).join("")}
      ${decisions.map(item => `<li>${action("decision-jump", esc(item.question), `data-decision="${item.id}"`)}</li>`).join("")}</ul></section>` : ""}
      <section class="wr-progress"><h3>分工进度 <span>${done}/${jobs.length}${isOpen(room) ? "" : ` · ${ending[room.close_reason]}`}</span></h3><progress value="${done}" max="${Math.max(1, jobs.length)}" aria-label="分工完成进度"></progress>
      ${jobs.length ? `<ul>${jobs.map(item => assignmentHTML(room, item)).join("")}</ul>` : '<p class="wr-muted">还没有分工</p>'}</section>
      ${room.budget || room.usage_pending ? `<section class="wr-budget"><h3>预算</h3>
        ${room.budget ? `<p>已用 ${count(room.budget.spent)} / ${count(room.budget.tokens)} tokens</p>` : "<p>未设上限</p>"}
        ${room.usage_pending ? `<p>另有 ${count(room.usage_pending)} 次用量待确认</p>` : ""}</section>` : ""}
      <div class="wr-drawers">${latest(room) ? action("full", `<span>计划书 v${room.draft_version}</span><span>${canApprove(room) ? "有修订待敲定 ›" : "看全文 ›"}</span>`) : ""}
      ${action("internal", `<span>内部交流</span><span>${internal.length} 条 ›</span>`)}${action("members", `<span>参战成员</span><span>${participants(room).length} 人 ›</span>`)}</div>
      ${steps.length ? `<details class="wr-steps" data-wr-card="steps"><summary>开局记录 · ${steps.length} 项</summary><ul>${steps.map(event => `<li>${esc(launchStep[event.detail.step])}${event.detail.step === "goal" ? ` · ${esc(event.detail.target)}` : ""}</li>`).join("")}</ul></details>` : ""}
      </div>`;
  }

  function assignmentHTML(room, job) {
    const executions = jobExecutions(room, job.goal_id), unread = executions.reduce((n, item) => n + item.unread, 0);
    const label = `<span><strong>${esc(job.title)}</strong>${job.owner_name ? `<small>${esc(job.owner_name)}</small>` : ""}</span>
      <span class="wr-job-state"><span data-status="${esc(job.status)}">${esc(itemStatus[job.status])}</span>
      ${executions.length ? `<small>${unread ? `${count(unread)} 条未读` : "执行记录"} ›</small>` : ""}</span>`;
    return `<li>${executions.length ? action("history", label, `data-execution="${esc(executions[0].id)}"`) : label}</li>`;
  }

  function historyEvent(event, owner) {
    const label = event.type === "assistant" || event.role === "assistant" ? owner :
      event.type === "tool_result" ? (event.is_error ? "工具报错" : "工具结果") :
      event.type === "error" ? "执行报错" : (event.sender || "收到的消息");
    const body = event.type === "assistant" ? event.content.map(block => block.type === "text" ?
      CrewReportMarkdown.render(block.text) : `<p>调用工具 · ${esc(block.name)}</p>`).join("") :
      event.type === "tool_result" ? `<pre>${esc(typeof event.content === "string" ? event.content : JSON.stringify(event.content, null, 2))}</pre>` :
      CrewReportMarkdown.render(event.text);
    return `<article class="wr-note"><div class="wr-history-meta"><strong>${esc(label)}</strong><time datetime="${esc(event.at)}">${esc(K.clock(event.at))}</time></div><div class="crew-md-doc">${body}</div></article>`;
  }

  function historyHTML(ctx) {
    const h = ctx.history, executions = jobExecutions(ctx.room, h.goalId);
    return `<section class="wr-history"><h3>${esc(h.title)}</h3>
      ${executions.length > 1 ? `<nav aria-label="选择执行">${executions.map(item => action("history", `第 ${item.round} 轮 · 第 ${item.generation} 次${item.unread ? ` · ${item.unread} 条未读` : ""}`,
        `data-execution="${esc(item.id)}" aria-pressed="${item.id === h.id}"`)).join("")}</nav>` : ""}
      ${h.error ? `<p class="wr-error" role="alert">${esc(h.error)}</p>${action("history-more", "重试")}` :
        h.hasMore ? action("history-more", "看更早的记录", h.loading ? "disabled" : "") : ""}
      ${h.events ? h.events.map(event => historyEvent(event, h.owner_name)).join("") || '<p class="wr-muted">还没有执行记录</p>' : ""}
      ${h.loading ? '<p class="wr-muted" role="status">正在读取执行记录…</p>' : ""}</section>`;
  }

  function summary(ctx) {
    const room = ctx.room;
    if (showsPlan(room)) {
      const plan = latest(room);
      const n = plan ? P.pending(plan).length : 0;
      return plan ? `计划书 v${plan.version}${n ? ` · ${n} 项等你定` : ""}` : "等第一版计划书";
    }
    const items = assignments(room), done = items.filter(item => item.status === "done").length;
    if (!isOpen(room)) return `进度 ${done}/${items.length} · ${ending[room.close_reason]}`;
    const blocked = room.board.filter(item => item.status === "blocked").length, waiting = room.open_decisions;
    return `进度 ${done}/${items.length}${blocked ? ` · ${blocked} 项卡住` : ""}${waiting ? ` · ${waiting} 项等你定` : ""}`;
  }

  function headerHTML(ctx) {
    const room = ctx.room, open = isOpen(room);
    const actions = !open ? "" : (room.legacy ? action("close", "退出旧房") :
      preparing(room) ? action("cancel", "撤销筹备") : "") + action("stop", "叫停");
    const label = open ? (preparing(room) ? "筹备中" : "作战中") : ending[room.close_reason];
    return `<a class="cx-btn wr-back" href="${roomLink(0)}" data-wr-route="0">‹ 作战室</a><div class="wr-heading"><h1>${esc(room.title)}</h1><p>${esc(room.goal)} · 领头 ${esc(room.lead_name)}</p></div>
      <span class="wr-phase" data-state="${open ? CrewGlobalRail.roomState([room]) : "closed"}">${esc(label)}</span><div class="wr-room-actions">${actions}</div>`;
  }

  function membersHTML(ctx) {
    const room = ctx.room;
    return `<ul class="wr-participants">${participants(room).map(item => {
      const live = ctx.state.leaders.find(person => person.session_id === item.session_id);
      const lead = item.session_id === room.lead_session_id;
      const working = lead ? room.lead_working_room_id : item.working_room_id;
      return `<li>${ctx.avatar(item.crew_name, 28, true)}<strong>${esc(item.crew_name)}</strong>
        ${lead ? "<span>领头</span>" : ""}${isOpen(room) && working === room.id ? '<small class="room-working-tag">作战室中</small>' : isOpen(room) && live ? `<small>${esc(K.presence(live).text)}</small>` : ""}</li>`;
    }).join("")}</ul>`;
  }

  function render(ctx, follow = false) {
    if (ctx.id === 0) { put(ctx.list, roomListHTML(ctx)); return; }
    if (!ctx.room) return;
    const {room} = ctx, {main, internal} = conversation(room), open = isOpen(room), showPlan = showsPlan(room);
    ctx.host.dataset.phase = showPlan ? "preparing" : "active";
    ctx.host.dataset.collapsed = String(ctx.collapsed);
    put(ctx.head, headerHTML(ctx));
    put(ctx.bar, action("panel", `<span>${esc(summary(ctx))}</span><span>${showPlan ? "看计划书" : "展开战况"} ›</span>`));
    put(ctx.toggle, showPlan ? "" : action("collapse", ctx.collapsed ? "展开战况" : "收起战况", `aria-expanded="${!ctx.collapsed}"`));
    const b = room.budget;
    ctx.notice.hidden = !open && !room.summary || open && !b?.paused_at;
    put(ctx.notice, open && b?.paused_at ? `<div><strong>预算用完了，已暂停</strong><small>已用 ${count(b.spent)} / ${count(b.tokens)} tokens</small></div><div>${action("end_budget","结束")}${action("budget","追加预算")}</div>` : room.summary ? `<p>${esc(room.summary)}</p>` : "");
    put(ctx.feed, timelineHTML(ctx, main));
    if (follow) ctx.scroll.scrollTop = ctx.scroll.scrollHeight;
    put(ctx.aside, showPlan ? planHTML(ctx) : statusHTML(ctx, internal));
    renderPanelDialog(ctx);
    applyPlanHighlights(ctx);
    ctx.composer.hidden = !open;
    ctx.input.placeholder = `和${room.lead_name}说…`;
    ctx.send.disabled = ctx.sending || !ctx.input.value.trim() || !open;
    put(ctx.reply, ctx.replyTo ? `<span>回复 ${esc(ctx.replyTo.speaker_name)} #${ctx.replyTo.id}</span>${action("unreply", "取消引用")}` : "");
    ctx.reply.hidden = !ctx.replyTo;
    ctx.error.textContent = ctx.readError;
  }

  function renderPanelDialog(ctx) {
    const dialog = ctx.panelDialog;
    if (!dialog.open) return;
    const type = ctx.panelType;
    const showPlan = type === "plan" || (type === "panel" && showsPlan(ctx.room));
    const plan = showPlan && selectedPlan(ctx);
    const title = showPlan ? `作战计划书${plan ? ` v${plan.version}` : ""}` :
      ({internal:"内部交流", members:"参战成员", panel:"战况", history:"执行记录"})[type];
    const {internal} = conversation(ctx.room);
    const body = showPlan ? planHTML(ctx, true) : type === "panel" ? statusHTML(ctx, internal, true) :
      type === "members" ? membersHTML(ctx) : type === "history" ? historyHTML(ctx) :
        internal.map(message => messageHTML(ctx, message, true)).join("") || '<p class="wr-panel-empty">还没有内部交流</p>';
    put(ctx.panelTitle, esc(title));
    put(ctx.panelContent, body);
    dialog.classList.toggle("wr-sheet-drawer", !showPlan && type !== "panel");
  }

  function applyPlanHighlights(ctx) {
    const plan = selectedPlan(ctx);
    if (!plan) return;
    const marks = plan.version > ctx.dismissedVersion ? new Map(planChanges(ctx, plan).filter(change => change.type !== "删").map(change => [change.key, change.type])) : new Map();
    for (const element of ctx.host.querySelectorAll(".wr-plan-document [data-wr-block]")) {
      const mark = marks.get(element.dataset.wrBlock);
      element.classList.toggle("wr-mark-faded", !mark && element.hasAttribute("data-mark"));
      if (mark) element.dataset.mark = mark;
    }
    if (ctx.invalidLine) {
      const item = planData(ctx, plan).blocks.find(block => block.line <= ctx.invalidLine && block.end >= ctx.invalidLine);
      const document = planRoot(ctx).querySelector(".wr-plan-document");
      if (item && document) jumpTo(document, item.key);
      ctx.invalidLine = null;
    }
  }

  function jumpTo(document, key) {
    const target = [...document.querySelectorAll("[data-wr-block]")].find(node => node.dataset.wrBlock === key);
    target.scrollIntoView({block:"center", behavior:"instant"});
    target.classList.remove("wr-flash"); void target.offsetWidth; target.classList.add("wr-flash");
  }

  function anchorLocation(ctx, version, block) {
    const plans = ctx.room.plans.filter(plan => plan.version >= version).sort((a, b) => a.version - b.version);
    const original = planData(ctx, plans[0]).blocks.find(item => item.key === block);
    if (original && !original.stable) {
      let key = block;
      for (let i = 1; i < plans.length; i += 1) {
        const {positions} = P.compare(planData(ctx, plans[i - 1]), planData(ctx, plans[i]));
        key = positions.get(key);
        if (key === undefined) return {deletedVersion:plans[i].version};
      }
      return {key};
    }
    const hasBlock = plan => block === "budget" ? Boolean(planBudget(plan, previousPlan(ctx, plan))) :
      planData(ctx, plan).blocks.some(item => item.key === block);
    if (hasBlock(latest(ctx.room))) return {key:block};
    return {deletedVersion:plans.find(plan => !hasBlock(plan)).version};
  }

  function openAnchor(ctx, version, block) {
    ctx.planVersion = block === undefined ? version : null;
    ctx.compare = false;
    if (!showsPlan(ctx.room) || narrow()) showPanel(ctx, "plan");
    else render(ctx);
    if (block === undefined) return;

    const {key, deletedVersion} = anchorLocation(ctx, version, block);
    if (key !== undefined) {
      jumpTo(planRoot(ctx).querySelector(".wr-plan-document"), key);
    } else {
      ctx.toast(`这一段在 v${deletedVersion} 已删除`, null, ctx.panelDialog.open ? ctx.panelToast : undefined);
    }
  }

  function showPanel(ctx, type, reset = false) {
    if (reset) { ctx.planVersion = null; ctx.compare = false; }
    ctx.panelType = type;
    if (!ctx.panelDialog.open) ctx.panelDialog.showModal();
    render(ctx);
    ctx.panelDialog.querySelector("[data-wr=hide-panel]").focus();
  }

  function approvalError(error) {
    const {code, detail} = error.details ?? {};
    if (code === "version_conflict") return "计划变了。请看最新版，再确认一次。";
    if (code === "missing_recommendation") return "还有问题没有推荐值，请领头补上再敲定。";
    if (code === "invalid_criteria" || code === "invalid_plan") {
      const label = code === "invalid_criteria" ? "完成标准没写对" : "计划书有一处没写对";
      const line = detail?.line ? `（第 ${detail.line} 行）` : "";
      return `${label}${line}。请领头改好再敲定。`;
    }
    return error.message;
  }

  function confirm(ctx, kind, args = null) {
    if (ctx.writing) return;
    const {room} = ctx;
    const n = kind === "approve" ? Object.keys(args.recommendations).length : 0;
    const approvalTitle = kind === "approve" ? (preparing(room) ? "敲定，开打" : "敲定修订") : "";
    const budgetField = kind === "budget" ? field("新的总上限（tokens）",
      `<input name="budget" type="number" min="${Math.max(room.budget.spent, room.budget.tokens) + 1}" step="1" required>`) : "";
    const knobs = {
      approve:[approvalTitle, n ? `还有 ${n} 项没定，按领头推荐直接${preparing(room) ? "开打" : "敲定修订"}？` : ""],
      stop:["叫停作战室", "现在叫停，本房正在跑的任务会停下。"],
      cancel:["撤销筹备", "撤销后，这份筹备会留在历史里。"],
      end_budget:["结束作战室", "按当前预算结束，房间会留在历史里。"],
      budget:["追加预算", budgetField],
      close:["退出旧房", field("收尾结论", '<textarea name="summary" maxlength="200" required></textarea>')],
      open:["开作战室", field("名字", '<input name="title" maxlength="30" required>') + field("目标", '<textarea name="goal" maxlength="200" required></textarea>') +
        field("领头", `<select name="lead" required><option value="">选择领头</option>${ctx.state.leaders.map(item => `<option value="${esc(item.session_id)}">${esc(item.name)}</option>`).join("")}</select>`)]
    };
    const [title, description] = knobs[kind];
    ctx.confirmation = {kind, args};
    if (kind === "approve" && !n) { void submit(ctx); return; }
    ctx.form.innerHTML = `<h2 id="wr-confirm-title">${title}</h2>${description.startsWith("<") ? description : `<p>${description}</p>`}
      <p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts">${action("dismiss", "返回")}<button type="submit" class="cx-btn cx-primary">${kind === "approve" ? (preparing(room) ? "按推荐开打" : "按推荐敲定") : title}</button></div>`;
    ctx.confirmDialog.showModal();
  }

  async function submit(ctx) {
    if (ctx.writing) return;
    const {kind, args} = ctx.confirmation;
    const form = ctx.form, elements = form.elements;
    const link = kind === "approve" ? "plan.approve" : kind;
    const data = kind === "approve" ? args : kind === "open" ? {
      title:elements.title.value.trim(), goal:elements.goal.value.trim(), lead:elements.lead.value
    } : {room:ctx.id, ...(kind === "budget" ? {budget_tokens:Number(elements.budget.value)} :
      kind === "close" ? {summary:elements.summary.value.trim()} : {})};
    const button = form.querySelector("[type=submit]");
    if (button) button.disabled = true;
    ctx.writing = true;
    let result;
    try { result = await ctx.api(roomPath + link, data); }
    catch (error) {
      if (!current(ctx)) return;
      if (kind === "approve") {
        if (ctx.confirmDialog.open) ctx.confirmDialog.close();
        ctx.approvalError = approvalError(error);
        ctx.invalidLine = error.details?.detail?.line;
        ctx.toast(ctx.approvalError);
        if (error.details?.code === "version_conflict") {
          ctx.planVersion = null;
          ctx.compare = false;
          await load(ctx);
        }
        render(ctx);
      } else if (kind === "open" && error.details?.code === "room_occupied") {
        ctx.confirmDialog.close();
        ctx.toast(error.message);
        ctx.openRoom(error.details.detail.room_id);
      } else form.querySelector(".wr-dialog-error").textContent = error.message;
      return;
    } finally {
      ctx.writing = false;
      if (button) button.disabled = false;
    }
    if (!current(ctx)) return;
    if (ctx.confirmDialog.open) ctx.confirmDialog.close();
    if (kind === "open") { ctx.refreshRooms(); ctx.openRoom(result.id); return; }
    ctx.approvalError = "";
    ctx.planVersion = null;
    ctx.compare = false;
    if (ctx.panelDialog.open) ctx.panelDialog.close();
    await load(ctx, true);
    if (current(ctx)) ctx.onChanged();
  }

  async function load(ctx, follow = false) {
    const seq = ++ctx.readSeq;
    try {
      const room = await ctx.api(`${roomPath}get?id=${ctx.id}&limit=200`);
      if (!current(ctx) || seq !== ctx.readSeq) return;
      const first = !ctx.room, bottom = atBottom(ctx.scroll);
      ctx.room = room;
      if (first) {
        ctx.readFrom = room.read_id;
        const lastUser = room.messages.filter(message => message.speaker_session_id === "user").at(-1);
        if (lastUser) ctx.dismissedVersion = Math.max(0, ...room.plans.filter(plan => Date.parse(plan.created_at) < Date.parse(lastUser.created_at)).map(plan => plan.version));
      }
      ctx.readId = Math.max(ctx.readId, room.read_id);
      ctx.readError = "";
      ctx.loading.hidden = true;
      ctx.surface.hidden = false;
      render(ctx, follow || bottom);
      void read(ctx);
      const h = ctx.history, execution = h && room.executions.find(item => item.id === h.id);
      if (historyOpen(ctx, h) && h.events && execution?.latest_seq > (h.events.at(-1)?.seq ?? 0)) void loadHistory(ctx);
    } catch (error) {
      if (!current(ctx) || seq !== ctx.readSeq) return;
      ctx.readError = error.message;
      ctx.error.textContent = ctx.readError;
      ctx.loading.hidden = true;
    }
  }

  function historyOpen(ctx, history) {
    return current(ctx) && history && ctx.history === history && ctx.panelDialog.open && ctx.panelType === "history";
  }

  async function readHistory(ctx) {
    const h = ctx.history;
    if (!historyOpen(ctx, h) || h.reading || !h.seq || h.seq <= h.readSeq || document.visibilityState !== "visible") return;
    const seq = h.seq;
    h.reading = true;
    try {
      const read = await ctx.api(roomPath + "mark_read", {room:ctx.id, execution_id:h.id, seq});
      h.readSeq = read.read_seq;
      if (current(ctx)) {
        await load(ctx);
        if (current(ctx)) ctx.onChanged();
      }
    } catch (error) {
      if (historyOpen(ctx, h)) ctx.toast(error.message, null, ctx.panelToast);
    } finally {
      h.reading = false;
      if (h.seq > seq && historyOpen(ctx, h)) void readHistory(ctx);
    }
  }

  async function loadHistory(ctx, before = null) {
    const h = ctx.history;
    if (h.loading) return;
    h.loading = true;
    h.error = "";
    h.before = before;
    renderPanelDialog(ctx);
    const query = new URLSearchParams({id:ctx.id, execution_id:h.id, limit:50});
    if (before !== null) query.set("before_seq", before);
    try {
      const {executions} = await ctx.api(`${roomPath}get?${query}`);
      if (!historyOpen(ctx, h)) return;
      const page = executions.find(item => item.id === h.id);
      const height = ctx.panelContent.scrollHeight, top = ctx.panelContent.scrollTop;
      h.events = before === null ? page.events : [...page.events, ...h.events];
      h.hasMore = page.has_more;
      h.readSeq = page.read_seq;
      h.seq = h.events.at(-1)?.seq;
      h.loading = false;
      renderPanelDialog(ctx);
      ctx.panelContent.scrollTop = before === null ? ctx.panelContent.scrollHeight : top + ctx.panelContent.scrollHeight - height;
      void readHistory(ctx);
    } catch (error) {
      if (historyOpen(ctx, h)) {
        h.loading = false;
        h.error = error.message;
        renderPanelDialog(ctx);
      }
    }
  }

  async function read(ctx) {
    const room = ctx.room;
    if (!room || !isOpen(room) || !room.unread || ctx.reading || room.latest_id <= ctx.readId ||
      document.visibilityState !== "visible" || !atBottom(ctx.scroll)) return;
    ctx.reading = true;
    try {
      const result = await ctx.api(roomPath + "mark_read", {room:room.id, id:room.latest_id});
      ctx.readId = Math.max(ctx.readId, result.read_id);
    } catch (error) { if (current(ctx)) ctx.toast(error.message); }
    finally { ctx.reading = false; }
    if (current(ctx) && ctx.room.latest_id > room.latest_id) void read(ctx);
  }

  async function send(ctx) {
    const body = ctx.input.value.trim();
    if (ctx.sending || !body || !isOpen(ctx.room)) return;
    const version = ctx.room.draft_version, original = ctx.input.value, reply = ctx.replyTo;
    ctx.sending = true; ctx.send.disabled = true;
    try {
      const {message} = await ctx.api(roomPath + "post", {room:ctx.id, body, to:[ctx.room.lead_session_id],
        ...(reply ? {reply_to:reply.id} : {})});
      if (!current(ctx)) return;
      ctx.dismissedVersion = Math.max(ctx.dismissedVersion, version);
      if (ctx.input.value === original) ctx.input.value = "";
      if (ctx.replyTo === reply) ctx.replyTo = null;
      if (!ctx.room.messages.some(item => item.id === message.id)) ctx.room.messages.push(message);
      render(ctx, true);
      await load(ctx, true);
    } catch (error) { if (current(ctx)) ctx.toast(error.message); }
    finally { ctx.sending = false; if (current(ctx)) render(ctx); }
  }

  function reference(ctx, prefix) {
    if (ctx.panelDialog.open) ctx.panelDialog.close();
    ctx.replyTo = null;
    const text = ctx.input.value.replace(/^关于 [^：\n]+：\s?/, "");
    ctx.input.value = prefix + text;
    render(ctx);
    ctx.input.focus();
  }

  function click(ctx, button) {
    const {wr, version, block, message, entry, decision, execution} = button.dataset;
    switch (wr) {
    case "reload": if (ctx.id === 0) ctx.refreshRooms(); else void load(ctx); break;
    case "collapse": ctx.collapsed = !ctx.collapsed; render(ctx); break;
    case "panel": showPanel(ctx, "panel", true); break;
    case "full": showPanel(ctx, "plan", true); break;
    case "internal": case "members": showPanel(ctx, wr); break;
    case "history": {
      if (historyOpen(ctx, ctx.history) && execution === ctx.history.id) break;
      const stream = ctx.room.executions.find(item => item.id === execution);
      const job = ctx.room.board.find(item => item.goal_id === stream.goal_id);
      const {title, owner_name} = job ?? ctx.history;
      ctx.history = {id:execution, goalId:stream.goal_id, title, owner_name};
      showPanel(ctx, "history");
      void loadHistory(ctx);
      break;
    }
    case "history-more": void loadHistory(ctx, ctx.history.error ? ctx.history.before : ctx.history.events[0].seq); break;
    case "hide-panel": ctx.panelDialog.close(); break;
    case "latest": ctx.planVersion = null; ctx.compare = false; render(ctx); break;
    case "compare": ctx.compare = !ctx.compare; render(ctx); break;
    case "jump": openAnchor(ctx, Number(version), block); break;
    case "revision-more": {
      const value = Number(version);
      if (ctx.expandedVersions.has(value)) ctx.expandedVersions.delete(value);
      else ctx.expandedVersions.add(value);
      put(ctx.feed, timelineHTML(ctx, conversation(ctx.room).main));
      ctx.feed.querySelector(`[data-wr="revision-more"][data-version="${value}"]`).focus({preventScroll:true});
      break;
    }
    case "reference": reference(ctx, `关于 ${entry}：`); break;
    case "reply":
      ctx.replyTo = ctx.room.messages.find(item => item.id === Number(message)); render(ctx); ctx.input.focus(); break;
    case "unreply": ctx.replyTo = null; render(ctx); ctx.input.focus(); break;
    case "decision-jump":
      if (ctx.panelDialog.open) ctx.panelDialog.close();
      ctx.feed.querySelector(`#wr-decision-${Number(decision)}`)?.scrollIntoView({block:"center"});
      break;
    case "origin": ctx.openChannel(ctx.room.origin.id); break;
    case "approve": {
      const plan = ctx.room.plans.find(item => item.version === Number(version));
      if (plan) confirm(ctx, wr, P.approval(ctx.room, plan));
      break;
    }
    case "dismiss": if (!ctx.writing) ctx.confirmDialog.close(); break;
    default: confirm(ctx, wr);
    }
  }

  const dialog = `<dialog class="wr-pane-dialog" aria-labelledby="wr-pane-title"><div class="wr-pane-title"><strong id="wr-pane-title"></strong>${action("hide-panel", "关闭")}</div><div class="wr-pane-content"></div><div class="wr-pane-toast"></div></dialog>
    <dialog class="wr-confirm" aria-labelledby="wr-confirm-title"><form class="wr-dialog"></form></dialog>`;
  const layout = `<div class="wr-load"><p class="wr-error" role="alert"></p>${action("reload", "重新读取")}</div><p class="wr-loading" role="status">正在读取作战室…</p>
    <div class="wr-surface" hidden><header class="wr-main-head"></header><div class="wr-panelbar"></div><div class="wr-notice" role="status" hidden></div>
    <div class="wr-layout"><section class="cx-feed wr-feed" aria-label="和领头的对话"><div class="wr-chat-head"><h2>和领头的对话</h2><div class="wr-toggle"></div></div>
    <div class="cx-scroll" tabindex="0" aria-label="房内对话"><div class="cx-inner wr-timeline"></div></div>
    <div class="cx-composer-wrap"><div class="wr-reply" hidden></div><form class="cx-composer"><textarea rows="2" aria-label="给领头发言" required></textarea><div class="cx-c-row"><button type="submit" class="cx-btn cx-primary wr-send">发送</button></div></form></div></section><aside class="wr-plan-panel"></aside></div></div>`;

  function mount(options) {
    unmount();
    const ctx = {...options, room:null, readSeq:0, readId:0, readFrom:0, readError:"", approvalError:"",
      documents:new Map(), expandedVersions:new Set(), planVersion:null, dismissedVersion:0, compare:false, collapsed:false,
      writing:false, reading:false, sending:false, replyTo:null};
    active = ctx;
    const host = ctx.host;
    host.innerHTML = (ctx.id === 0 ? '<div class="wr-directory"></div>' : layout) + dialog;
    const selectors = {list:".wr-directory", error:".wr-error", loading:".wr-loading", surface:".wr-surface", head:".wr-main-head",
      bar:".wr-panelbar", toggle:".wr-toggle", notice:".wr-notice", feed:".wr-timeline", scroll:".cx-scroll", aside:".wr-plan-panel",
      composer:".cx-composer-wrap", input:"textarea", send:".wr-send", reply:".wr-reply", panelDialog:".wr-pane-dialog",
      panelTitle:"#wr-pane-title", panelContent:".wr-pane-content", panelToast:".wr-pane-toast", confirmDialog:".wr-confirm", form:".wr-confirm form"};
    for (const [key, selector] of Object.entries(selectors)) ctx[key] = host.querySelector(selector);
    host.onclick = event => {
      const link = event.target.closest("[data-wr-route]");
      if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        ctx.openRoom(Number(link.dataset.wrRoute));
        return;
      }
      const button = event.target.closest("button[data-wr]");
      if (button && !button.disabled) click(ctx, button);
    };
    ctx.form.onsubmit = event => { event.preventDefault(); void submit(ctx); };
    ctx.confirmDialog.oncancel = event => { if (ctx.writing) event.preventDefault(); };
    ctx.panelDialog.onclose = () => {
      ctx.panelToast.replaceChildren();
      ctx.history = null;
      if (ctx.panelType === "panel" && !showsPlan(ctx.room)) ctx.planVersion = null;
    };
    if (ctx.id === 0) { render(ctx); return; }
    ctx.scroll.onscroll = () => void read(ctx);
    ctx.visibility = () => { void read(ctx); void readHistory(ctx); };
    document.addEventListener("visibilitychange", ctx.visibility);
    CrewBrowser.enableChatLists(ctx.input);
    ctx.input.oninput = () => { ctx.send.disabled = ctx.sending || !ctx.input.value.trim(); };
    ctx.input.onkeydown = event => {
      if (event.defaultPrevented || !CrewBrowser.chatSendKey(event)) return;
      event.preventDefault(); void send(ctx);
    };
    ctx.send.form.onsubmit = event => { event.preventDefault(); void send(ctx); };
    void load(ctx, true);
  }

  function update(state, rooms, roomsError) {
    const ctx = active;
    if (!ctx) return;
    Object.assign(ctx, {state, rooms, roomsError});
    render(ctx);
  }

  function changed(changes) {
    const ctx = active;
    if (!ctx || ctx.id === 0) return;
    if (changes === null || changes.some(change => change.type.startsWith("war_room.") && Number(change.entity) === ctx.id ||
      change.type.startsWith("conversations.") && ctx.room?.phase === "active" && isOpen(ctx.room) ||
      change.type.startsWith("channel_message.") && (change.data?.room_id === ctx.id ||
        ctx.room?.messages.some(message => String(message.id) === String(change.entity))))) void load(ctx);
  }

  function unmount() {
    if (active?.visibility) document.removeEventListener("visibilitychange", active.visibility);
    active = null;
  }

  window.CrewWarRoom = {mount, update, changed, unmount};
})();
