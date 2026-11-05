"use strict";
(() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const supplementsOpen = new Set();
  const ended = new Set(["completed", "cancelled"]);

  // A workspace overview is a projection. Process existence and historical
  // goals must never be presented as evidence that work is currently running.
  function project({leaders, members, goals, todos, decisions}) {
    const people = [...leaders, ...members];
    const active = people.filter(person => person.status === "working");
    const dispatches = people.flatMap(person => (person.codex || []).filter(job => job.source === "skill_dispatch" && job.status === "running"));
    const open = goals.filter(goal => !ended.has(goal.status));
    const owners = new Set(goals.map(goal => goal.leader_session_id));
    return {
      activeCount: active.length + dispatches.length,
      openGoals: open,
      pastGoals: goals.filter(goal => ended.has(goal.status)),
      discussions: leaders.filter(person => !owners.has(person.session_id)),
      todos: todos.filter(todo => todo.status == null || todo.status === "open"),
      decisions: decisions.filter(decision => !decision.held),
      reviews: goals.filter(goal => goal.status === "awaiting_acceptance" && goal.acceptance_brief),
      deliveries: goals.filter(goal => goal.report_available && goal.status !== "awaiting_acceptance"),
    };
  }
  const attentionCount = data => {
    const model = project(data);
    return model.reviews.length + model.decisions.length + model.todos.length;
  };

  // Acceptance is the user's own step: put the report gist and the hands-on
  // check list first, then open the full report or the task to decide.
  function reviewCards(items, people) {
    return items.map(goal => {
      const brief = goal.acceptance_brief, steps = brief.user_test || [];
      const owner = people.find(person => person.session_id === goal.leader_session_id);
      const shown = steps.slice(0, 4);
      return `<article class="wh-review"><div class="wh-review-head"><span class="goal-status" data-status="awaiting_acceptance">待你验收</span><strong>${escape(goal.title)}</strong><span class="wh-review-owner">${escape(owner?.name || "负责人")}</span></div><p class="wh-review-summary">${escape(brief.summary)}</p><div data-home-supplements="${escape(goal.id)}">${window.CrewGoals.supplementsMarkup(brief.supplements_after_report, people, supplementsOpen.has(String(goal.id)))}</div>${shown.length ? `<div class="wh-review-test"><span>怎么实测</span><ol>${shown.map(step => `<li><span>${escape(step.step)}</span><span class="wh-review-expected">预期：${escape(step.expected)}</span></li>`).join("")}</ol>${steps.length > shown.length ? `<p class="wh-review-more">还有 ${steps.length - shown.length} 步，见完整报告</p>` : ""}</div>` : ""}<div class="wh-review-actions"><button class="btn primary" data-goal-act="delivery" data-goal-id="${escape(goal.id)}">打开报告</button><button class="btn ghost" data-act="goal-review" data-id="${escape(goal.id)}">去验收 →</button></div></article>`;
    }).join("");
  }

  function render(data, {taskCards, decisionCards, todoCards, approvalCount = 0, settingsIcon}) {
    const model = project(data);
    const attention = attentionCount(data) + approvalCount;
    const summary = model.activeCount ? `${model.activeCount} 个会话 / 派工执行中` : "从一个想法开始，把它一步步做完";
    const tasks = model.openGoals.length ? taskCards(model.openGoals) : "";

    const empty = !tasks && !model.discussions.length ? `<div class="wh-empty"><span class="wh-empty-mark" aria-hidden="true">＋</span><h2>这次想做点什么？</h2><p>先和 leader 聊聊。需求明确、得到你的执行同意后，就开始推进。</p><button class="btn primary" data-act="modal-leader">开始新对话</button></div>` : "";
    return `<header class="head wh-head"><div><div class="wh-eyebrow">工作区</div><h1 class="title">${escape(data.name)}</h1><p class="sub">${escape(summary)}</p></div><div class="right"><button data-local-control class="icon-btn" data-act="workspace-settings" title="当前工作区设置" aria-label="当前工作区设置">${settingsIcon}</button><button class="btn primary" data-act="modal-leader">＋ 新对话</button></div></header>
      <div class="page wh-page"><div class="wh-content">
        <nav class="wh-overview" aria-label="工作区动态"><span class="wh-overview-item"><strong>${model.openGoals.length}</strong>项任务</span><button class="wh-overview-item" data-act="all-tab" data-tab="working"><i class="dot ${model.activeCount ? "working" : ""}"></i><strong>${model.activeCount}</strong>个会话 / 派工执行中</button><span class="wh-overview-item"><strong>${attention}</strong>待你处理</span><button class="btn wh-people" data-act="all-tab" data-tab="tree"><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v7M5 10h14M5 10v5m14-5v5M8 3h8v4H8zM2 15h6v5H2zm14 0h6v5h-6z"/></svg>整体架构图</button></nav>
        <div class="wh-columns"><div class="wh-primary">${model.reviews.length ? `<section class="wh-reviews" aria-labelledby="wh-reviews"><div class="wh-section-head"><h2 id="wh-reviews">待你验收</h2><span>${model.reviews.length}</span></div><div class="wh-review-list">${reviewCards(model.reviews, [...data.leaders, ...data.members, ...(data.git_assistant === null ? [] : [data.git_assistant])])}</div></section>` : ""}<section class="wh-attention" aria-label="待你处理"><div class="wh-section-head"><h2>待你处理</h2><span>${model.decisions.length + model.todos.length + approvalCount}</span></div><div class="wh-attention-list">${model.decisions.map(item => `<div class="wh-action-item" data-kind="decision"><span class="wh-kind">拍板</span>${item.presentation_stage === "ready" ? "" : `<strong class="wh-action-title">${escape(item.question)}</strong>`}${decisionCards([item])}</div>`).join("")}${model.todos.map(item => `<div class="wh-action-item" data-kind="todo"><span class="wh-kind">TODO</span>${todoCards([item])}</div>`).join("")}${approvalCount ? `<button class="wh-permission" data-act="all-tab" data-tab="native-approvals">${approvalCount} 项执行权限需要确认 →</button>` : ""}${!model.decisions.length && !model.todos.length && !approvalCount ? '<p class="wh-quiet">暂时没有待你处理的事。</p>' : ""}</div></section></div>
        <aside class="wh-work" aria-labelledby="wh-tasks"><div class="wh-section-head"><h2 id="wh-tasks">任务列表</h2><span>${model.openGoals.length}</span></div>${tasks}${empty}${model.discussions.length ? `<div class="wh-discussions">${model.discussions.map(person => `<button class="wh-discussion-row" data-act="leader" data-id="${escape(person.session_id)}"><strong>${escape(person.name)}</strong><span>${escape(person.goal)}</span><span>继续对话 ›</span></button>`).join("")}</div>` : ""}${model.pastGoals.length ? `<details class="wh-history"><summary>已结束的任务 · ${model.pastGoals.length}</summary>${taskCards(model.pastGoals)}</details>` : ""}</aside></div>
        ${model.deliveries.length ? `<section class="wh-deliveries" aria-labelledby="wh-deliveries"><div class="wh-section-head"><h2 id="wh-deliveries">可以查看的成果</h2><span>${model.deliveries.length}</span></div><div class="wh-delivery-list">${model.deliveries.map(goal => `<button class="wh-delivery" data-goal-act="delivery" data-goal-id="${escape(goal.id)}"><span class="wh-delivery-icon" aria-hidden="true">↗</span><span><strong>${escape(goal.title)}</strong><span>第 ${escape(goal.current_round || 1)} 轮 · 查看交付与文档</span></span></button>`).join("")}</div></section>` : ""}
      </div></div>`;
  }
  function toggleSupplements(id) {
    const key = String(id);
    if (supplementsOpen.has(key)) supplementsOpen.delete(key); else supplementsOpen.add(key);
  }
  window.CrewWorkspaceHome = {project, render, toggleSupplements, attentionCount};
})();
