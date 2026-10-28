"use strict";
(() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
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
      deliveries: goals.filter(goal => goal.report_available),
    };
  }

  function decisionLinks(items, leaders) {
    return items.map(item => {
      const owner = leaders.find(person => person.session_id === item.leader_session_id || person.session_id === item.session_id);
      return `<article class="wh-attention-row"><span class="wh-attention-copy"><strong>${escape(item.title || item.question || item.summary || "有一件事需要你拍板")}</strong><span>${escape(owner?.name || "原任务")}</span></span>${owner ? `<button class="btn ghost" data-act="leader" data-id="${escape(owner.session_id)}">去对话</button>` : '<span class="note">负责人已归档</span>'}</article>`;
    }).join("");
  }

  function discussions(items) {
    return items.map(person => `<button class="wh-discussion" data-act="leader" data-id="${escape(person.session_id)}"><span class="wh-discussion-head"><span class="wh-monogram" aria-hidden="true">${escape(Array.from(person.name || "聊")[0])}</span><strong>${escape(person.name)}</strong><span class="wh-runtime">${escape(person.runtime === "claude" ? "CC" : person.runtime === "codex" ? "Codex" : person.runtime)}</span></span><span class="wh-discussion-text">${escape(person.goal || "继续讨论，逐步明确要完成的事")}</span><span class="wh-discussion-foot">${person.status === "working" ? "正在推进" : person.status === "waiting" ? "等你回应" : "进入对话"}<span aria-hidden="true">↗</span></span></button>`).join("");
  }

  function render(data, {taskCards, todoCards, approvalCount = 0}) {
    const model = project(data);
    const attention = model.decisions.length + model.todos.length + approvalCount;
    const summary = model.activeCount ? `${model.activeCount} 项工作正在推进` : "从一个想法开始，把它一步步做完";
    const tasks = model.openGoals.length ? taskCards(model.openGoals) : "";
    const discussion = discussions(model.discussions);
    const empty = !tasks && !discussion ? `<div class="wh-empty"><span class="wh-empty-mark" aria-hidden="true">＋</span><h2>这次想做点什么？</h2><p>先和 leader 聊聊。需求明确、得到你的执行同意后，就开始推进。</p><button class="btn primary" data-act="modal-leader">开始新对话</button></div>` : "";
    return `<header class="head wh-head"><div><div class="wh-eyebrow">工作区</div><h1 class="title">${escape(data.name)}</h1><p class="sub">${escape(summary)}</p></div><div class="right"><button class="btn ghost" data-act="personal-notes">快速想法</button><button class="btn primary" data-act="modal-leader">＋ 新对话</button></div></header>
      <div class="page wh-page"><div class="wh-content">
        <nav class="wh-overview" aria-label="工作区动态"><span class="wh-overview-item"><strong>${model.openGoals.length}</strong>项任务</span><button class="wh-overview-item" data-act="all-tab" data-tab="working"><i class="dot ${model.activeCount ? "working" : ""}"></i><strong>${model.activeCount}</strong>正在推进</button><span class="wh-overview-item"><strong>${attention}</strong>待你处理</span><button class="wh-overview-item wh-people" data-act="all-tab" data-tab="tree">成员与协作 ↗</button></nav>
        <div class="wh-columns"><section class="wh-work" aria-labelledby="wh-tasks"><div class="wh-section-head"><h2 id="wh-tasks">任务与对话</h2><span>${model.openGoals.length + model.discussions.length}</span></div>${tasks}${discussion ? `<div class="wh-discussions">${discussion}</div>` : ""}${empty}
          ${model.pastGoals.length ? `<details class="wh-history"><summary>已结束的任务 · ${model.pastGoals.length}</summary>${taskCards(model.pastGoals)}</details>` : ""}
        </section><aside class="wh-personal" aria-label="待我处理"><section class="wh-attention"><div class="wh-section-head"><h2>待拍板</h2><span>${model.decisions.length + approvalCount}</span></div>${decisionLinks(model.decisions, data.leaders)}${approvalCount ? `<button class="wh-permission" data-act="all-tab" data-tab="native-approvals">${approvalCount} 项执行权限需要确认 →</button>` : ""}${!model.decisions.length && !approvalCount ? '<p class="wh-quiet">暂时没有需要你拍板的事。</p>' : ""}</section>
          <section class="wh-todos"><div class="wh-section-head"><h2>我的 TODO</h2><span>${model.todos.length}</span></div>${model.todos.length ? todoCards(model.todos) : '<p class="wh-quiet">需要你亲自动手的事项会放在这里。</p>'}</section>
        </aside></div>
        ${model.deliveries.length ? `<section class="wh-deliveries" aria-labelledby="wh-deliveries"><div class="wh-section-head"><h2 id="wh-deliveries">可以查看的成果</h2><span>${model.deliveries.length}</span></div><div class="wh-delivery-list">${model.deliveries.map(goal => `<button class="wh-delivery" data-goal-act="delivery" data-goal-id="${escape(goal.id)}"><span class="wh-delivery-icon" aria-hidden="true">↗</span><span><strong>${escape(goal.title)}</strong><span>第 ${escape(goal.current_round || 1)} 轮 · 查看交付与文档</span></span></button>`).join("")}</div></section>` : ""}
      </div></div>`;
  }
  window.CrewWorkspaceHome = {project, render, decisionLinks};
})();
