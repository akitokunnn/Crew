"use strict";
(() => {
  window.HeaderLabVariants = window.HeaderLabVariants || {};

  function render(ctx) {
    const {state, data, esc, icon, conversation} = ctx;
    const task = ctx.task || data.tasks.find(item => item.id === state.scope);
    const general = !task;
    const title = ctx.currentTitle || (state.longTitle ? data.longTaskTitle : task?.title);
    const stateNames = {queued:"待规划", running:"推进中", waiting:"等你确认", delivered:"待验收"};
    const status = ctx.statusHtml || `<span class="scope-status" data-status="${esc(state.status)}">${esc(stateNames[state.status] || state.status)}</span>`;
    const mode = state.mode === "auto" ? "自主推进" : "由我拍板";
    const followup = {
      running:"leader 正在推进当前任务，新的进展会回到这里。",
      waiting:"本任务有待确认的取舍，回复前可以先查看需求与验收。",
      delivered:"本轮成果已提交，可以核对结果，也可以在这里补充修改意见。"
    };
    const front = general
      ? `<h1 class="document-sr-only">${esc(data.leader.name)}的总会话</h1>`
      : `<section class="document-task-front" data-status="${esc(state.status)}" aria-label="当前任务">
          <div class="document-front-meta">
            <span class="document-object-name">当前任务</span>${status}
            <button class="document-mode" type="button" data-action="mode" aria-haspopup="menu" title="修改本任务的决策方式">${mode}${icon("chevron")}</button>
          </div>
          <h1>${esc(title)}</h1>
          ${state.status === "queued" ? `
            <p class="document-front-summary">${esc(task.summary || "核对这项任务的需求与验收，再决定如何推进。")}</p>
            <div class="document-start-row">
              <button class="button document-start" type="button" data-action="start">${icon("arrow-right")}开始推进</button>
              <p>发送任务消息也会开始执行。<button class="document-inline-link" type="button" data-action="scope" data-scope="general">先去总会话聊</button></p>
            </div>` : `
            <div class="document-front-followup"><p>${esc(followup[state.status] || "可以在这里继续任务对话。")}</p>
              <button class="document-inline-link" type="button" data-action="details">需求与验收${icon("arrow-right")}</button>
            </div>`}
        </section>`;
    const body = conversation.replace('<div data-conversation-content>', '<div data-conversation-content>' + front);

    return `<div class="variant-layout document-layout ${state.mobileNav ? "document-nav-open" : ""}">
      ${state.mobileNav ? '<button class="document-nav-backdrop" type="button" data-action="mobile-nav" data-nav-backdrop aria-label="关闭项目导航"></button>' : ""}
      <aside class="document-sidebar" id="document-project-navigation" data-variant-nav tabindex="-1" aria-label="项目导航">
        <div class="document-project"><span class="document-project-mark" aria-hidden="true">${icon("grid")}</span><strong>${esc(data.projectName)}</strong>
          <button class="icon-button mobile-only" type="button" data-action="mobile-nav" data-nav-close aria-label="关闭项目导航">${icon("close")}</button>
        </div>
        <nav class="document-project-nav" aria-label="工作区">
          <button type="button" data-action="task-list">${icon("grid")}工作台</button>
          <button type="button" data-action="scope" data-scope="general">${icon("chat")}总会话</button>
        </nav>
        <div class="document-team-label">Leader</div>
        <button class="document-leader-row" type="button" data-action="details" title="查看 leader 的完整职责">
          <span class="avatar document-avatar" aria-hidden="true">M</span><span><strong>${esc(data.leader.name)}</strong><small>${esc(data.leader.runtime)} · 本组对话</small></span>
        </button>
        <div class="document-sidebar-footer sidebar-utility-links">
          <button type="button" data-action="assistant">${icon("chat")}全局助手</button>
          <button type="button" data-action="preferences">${icon("more")}设置与偏好</button>
        </div>
      </aside>
      <main class="document-workspace">
        <header class="document-toolbar" data-main-header>
          <button class="icon-button mobile-only" type="button" data-action="mobile-nav" aria-label="打开项目导航" aria-controls="document-project-navigation" aria-expanded="${Boolean(state.mobileNav)}">${icon("menu")}</button>
          <button class="document-header-identity desktop-only" type="button" data-action="details" aria-label="查看 leader：${esc(data.leader.name)}">
            <span class="avatar document-header-avatar" aria-hidden="true">M</span><span>${esc(data.leader.shortName)}</span>
          </button>
          <span class="document-toolbar-divider desktop-only" aria-hidden="true"></span>
          <nav class="document-scope-nav" aria-label="会话导航">
            <button class="document-general ${general ? "is-current" : ""}" type="button" data-action="scope" data-scope="general" ${general ? 'aria-current="page"' : ""}>总会话</button>
            <button class="document-task-switch ${general ? "" : "is-current"}" type="button" data-action="task-list" aria-haspopup="dialog" title="切换任务${task ? '：' + esc(task.title) : ""}">
              <span>${general ? "任务" : esc(task.shortTitle)}</span>${icon("chevron")}
            </button>
          </nav>
          <div class="header-utilities document-utilities">
            <button class="document-details-button" type="button" data-action="details" aria-label="打开任务资料">${icon("info")}<span class="desktop-only">资料</span></button>
            <button class="icon-button" type="button" data-action="more" aria-label="更多会话操作" aria-haspopup="menu">${icon("more")}</button>
          </div>
        </header>
        <div class="conversation-slot">${body}</div>
      </main>
    </div>`;
  }

  window.HeaderLabVariants.document = {
    id:"document",
    name:"任务扉页",
    render
  };
})();
