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
      const sidebar = state.sidebar || {};
      const query = String(sidebar.query || "");
      const search = query.trim().toLocaleLowerCase();
      const statuses = ["waiting", "running", "queued", "delivered"];
      const filter = statuses.includes(sidebar.filter) ? sidebar.filter : "all";
      const collapsed = sidebar.collapsed || {};
      const tasks = data.tasks.map(item => ({...item, status: state.scope === item.id ? state.status : item.status}));
      const matching = tasks.filter(item => (filter === "all" || item.status === filter) && `${item.title} ${item.shortTitle || ""}`.toLocaleLowerCase().includes(search));
      const expanded = itemStatus => Boolean(search) || !(collapsed[itemStatus] ?? itemStatus === "delivered");
      const currentVisible = general || matching.some(item => item.id === state.scope && expanded(item.status));

      const taskRow = item => {
        const selected = state.scope === item.id;
        return `<button type="button" class="sidebar-scope sidebar-task ${selected ? "is-current" : ""}"
          data-action="scope" data-scope="${esc(item.id)}" data-status="${esc(item.status)}" title="${esc(item.title)}" aria-label="${esc(`${item.title}，${statusLabel(item.status)}`)}"${current(item.id)}>
          <span class="sidebar-task-dot" data-status="${esc(item.status)}" aria-hidden="true"></span>
          <span class="sidebar-task-copy"><span>${esc(item.shortTitle || item.title)}</span><small>${esc(statusLabel(item.status))}</small></span>
          ${selected ? `<span class="sidebar-current-mark" aria-hidden="true">${icon("chevron")}</span>` : ""}
        </button>`;
      };
      const groups = statuses.map(itemStatus => {
        const items = matching.filter(item => item.status === itemStatus);
        if (!items.length) return "";
        const open = expanded(itemStatus);
        return `<section class="sidebar-task-group" data-sidebar-group="${itemStatus}">
          <button type="button" class="sidebar-group-toggle" data-action="sidebar-toggle-group" data-status="${itemStatus}"
            aria-expanded="${open}" aria-controls="sidebar-group-${itemStatus}">
            ${icon("chevron")}<span>${esc(statusLabel(itemStatus))}</span><span class="sidebar-group-count">${items.length}</span>
          </button>
          <div class="sidebar-task-list" id="sidebar-group-${itemStatus}"${open ? "" : " hidden"}>${items.map(taskRow).join("")}</div>
        </section>`;
      }).join("");

      return `<div class="variant-layout sidebar-layout ${tasks.length > 12 ? "sidebar-many-tasks" : ""} ${state.mobileNav ? "navigation-is-open" : ""}">
        ${state.mobileNav ? '<button type="button" class="sidebar-mask" data-action="mobile-nav" aria-label="关闭会话导航"></button>' : ""}
        <aside class="sidebar-pane" id="sidebar-demo-navigation" aria-label="当前 leader 的会话">
          <div class="sidebar-top">
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
          <nav class="sidebar-conversations" aria-label="leader 总会话">
            <button type="button" class="sidebar-scope sidebar-general ${general ? "is-current" : ""}"
              data-action="scope" data-scope="general"${current("general")}>
              ${icon("chat")}<span>总会话</span>
            </button>
          </nav>
          </div>
          <section class="sidebar-tasks" aria-label="任务导航">
            <div class="sidebar-task-heading">
              <span>任务</span><span class="sidebar-total-count">${data.tasks.length}</span>
              <div class="sidebar-task-actions">
                ${!general ? `<button type="button" class="icon-button sidebar-locate-button" data-action="sidebar-locate" aria-label="定位当前任务" title="定位当前任务">${icon("search")}</button>` : ""}
                <button type="button" class="sidebar-all-tasks" data-action="task-list" aria-label="打开任务清单">任务清单${icon("arrow-right")}</button>
              </div>
            </div>
            <div class="sidebar-task-filters">
              <label class="sidebar-search">${icon("search")}<input id="sidebar-task-search" type="search" aria-label="搜索任务" placeholder="搜索任务…" value="${esc(query)}" autocomplete="off"></label>
              <select id="sidebar-status-filter" aria-label="筛选任务状态">${[["all", "全部状态"], ...statuses.map(value => [value, statusLabel(value)])].map(([value, label]) => `<option value="${value}"${filter === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>
            </div>
            <div class="sidebar-results" aria-live="polite"><span data-sidebar-result-count="${matching.length}">${search || filter !== "all" ? `匹配 ${matching.length} 项` : `${matching.length} 项任务`}</span>${search || filter !== "all" ? '<button type="button" data-action="sidebar-clear">重置筛选</button>' : '<span>按状态分组</span>'}</div>
            ${!currentVisible ? `<div class="sidebar-locate-note"><span>当前任务未显示</span><button type="button" data-action="sidebar-locate">定位当前任务${icon("arrow-right")}</button></div>` : ""}
            <nav class="sidebar-task-scroll" data-sidebar-scroll aria-label="按状态分组的任务">
              ${matching.length ? groups : '<div class="sidebar-empty" role="status"><strong>没有匹配的任务</strong><p>试试其他任务名称，或清除当前筛选。</p><button type="button" data-action="sidebar-clear">清除筛选</button></div>'}
            </nav>
          </section>
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
