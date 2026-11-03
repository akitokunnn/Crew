"use strict";
(() => {
  window.HeaderLabVariants = window.HeaderLabVariants || {};
  window.HeaderLabVariants.sidebar = {
    id: "sidebar",
    name: "侧栏即导航",
    render(ctx) {
      const {state, data, esc, icon, conversation, statusLabel} = ctx;
      const task = ctx.task || data.tasks.find(item => item.id === state.scope);
      const general = state.scope === "general";
      const title = general ? "总会话" : ctx.currentTitle || task.title;
      const status = general ? "" : ctx.statusHtml || `<span class="scope-status" data-status="${esc(state.status)}">${esc(statusLabel(state.status))}</span>`;
      const current = id => state.scope === id ? ' aria-current="page"' : "";

      return `<div class="variant-layout sidebar-layout ${state.mobileNav ? "navigation-is-open" : ""}">
        ${state.mobileNav ? '<button type="button" class="sidebar-mask" data-action="mobile-nav" aria-label="关闭会话导航"></button>' : ""}
        <aside class="sidebar-pane" id="sidebar-demo-navigation" aria-label="当前 leader 的会话">
          <div class="sidebar-brand">
            <span class="sidebar-crew-mark" aria-hidden="true">C<span></span></span>
            <span class="sidebar-brand-copy"><strong>Crew</strong><span>项目工作台</span></span>
            <button type="button" class="icon-button sidebar-close" data-action="mobile-nav" aria-label="关闭会话导航">${icon("close")}</button>
          </div>
          <button type="button" class="sidebar-workbench" data-action="task-list">
            ${icon("grid")}<span>工作台</span>${icon("arrow-right")}
          </button>

          <div class="sidebar-group-heading"><span>当前团队</span></div>
          <button type="button" class="sidebar-leader" data-action="details" title="查看 leader 说明">
            <span class="avatar sidebar-leader-avatar" aria-hidden="true">M</span>
            <span><strong>${esc(data.leader.name)}</strong><small>leader</small></span>
            ${icon("info")}
          </button>
          <nav class="sidebar-conversations" aria-label="总会话与任务切换">
            <button type="button" class="sidebar-scope sidebar-general ${general ? "is-current" : ""}"
              data-action="scope" data-scope="general"${current("general")}>
              ${icon("chat")}<span>总会话</span>
            </button>
            <div class="sidebar-task-heading"><span>任务</span><span>${data.tasks.length}</span></div>
            <div class="sidebar-task-list">${data.tasks.map(item => {
              const selected = state.scope === item.id;
              const itemStatus = selected ? state.status : item.status;
              return `<button type="button" class="sidebar-scope sidebar-task ${selected ? "is-current" : ""}"
                data-action="scope" data-scope="${esc(item.id)}" title="${esc(item.title)}"${current(item.id)}>
                <span class="sidebar-task-dot" data-status="${esc(itemStatus)}" aria-hidden="true"></span>
                <span class="sidebar-task-copy"><span>${esc(item.shortTitle || item.title)}</span><small>${esc(statusLabel(itemStatus))}</small></span>
                ${selected ? `<span class="sidebar-current-mark" aria-hidden="true">${icon("chevron")}</span>` : ""}
              </button>`;
            }).join("")}</div>
            <button type="button" class="sidebar-all-tasks" data-action="task-list">查看任务清单${icon("arrow-right")}</button>
          </nav>
          <div class="sidebar-foot sidebar-utility-links">
            <button type="button" data-action="assistant">${icon("chat")}<span>全局助手</span></button>
            <button type="button" data-action="preferences">${icon("more")}<span>偏好设置</span></button>
          </div>
        </aside>

        <main class="sidebar-main">
          <header class="sidebar-header" data-main-header>
            <button type="button" class="icon-button sidebar-menu" data-action="mobile-nav"
              aria-label="打开会话导航" aria-controls="sidebar-demo-navigation" aria-expanded="${Boolean(state.mobileNav)}">${icon("menu")}</button>
            <div class="sidebar-title-wrap">
              ${general ? `<span class="sidebar-mobile-owner">${esc(data.leader.shortName)}</span>` : ""}
              <h1 title="${esc(title)}">${esc(title)}</h1>
            </div>
            ${status}
            <div class="header-utilities sidebar-header-utilities">
              <button type="button" class="button sidebar-details" data-action="details" aria-label="打开资料与设置" title="资料与设置">
                ${icon("file")}<span>资料</span>
              </button>
              <button type="button" class="icon-button" data-action="more" aria-label="更多会话操作" title="更多会话操作">${icon("more")}</button>
            </div>
          </header>
          <div class="conversation-slot">${conversation}</div>
        </main>
      </div>`;
    }
  };
})();
