"use strict";
(() => {
  window.HeaderLabVariants = window.HeaderLabVariants || {};
  window.HeaderLabVariants.tabs = {
    id: "tabs",
    name: "会话页签",
    render(ctx) {
      const {state, data, esc, icon, conversation, statusLabel} = ctx;
      const activeTask = data.tasks.find(task => task.id === state.scope) || null;
      const leaderName = data.leader.shortName || data.leader.name;
      const currentTitle = state.scope === "general" ? "总会话" : ctx.currentTitle || activeTask?.title || "当前任务";
      const selectedStatus = state.scope === "general" ? "" : (ctx.statusHtml || `<span class="scope-status" data-status="${esc(state.status)}">${esc(statusLabel(state.status))}</span>`);
      const tabs = [{id:"general", shortTitle:"总会话", title:"总会话"}, ...data.tasks].map(task => {
        const selected = state.scope === task.id;
        const title = selected ? currentTitle : task.title;
        return `<button class="tabs-tab${selected ? " is-current" : ""}" type="button"
          id="tabs-scope-${esc(task.id)}" role="tab" aria-selected="${selected}" aria-controls="tabs-conversation"
          data-action="scope" data-scope="${esc(task.id)}" title="${esc(title)}">
          ${icon(task.id === "general" ? "chat" : "file")}
          <span class="tabs-tab-name">${esc(task.shortTitle || task.title)}</span>
          ${selected ? selectedStatus : ""}
        </button>`;
      }).join("");
      return `<div class="tabs-layout${state.mobileNav ? " is-mobile-nav-open" : ""}">
        ${state.mobileNav ? '<button class="tabs-side-backdrop" type="button" data-action="mobile-nav" aria-label="关闭项目导航"></button>' : ""}
        <aside class="tabs-project-sidebar" aria-label="项目导航" data-variant-nav tabindex="-1">
          <div class="tabs-side-brand"><span class="tabs-brand-mark" aria-hidden="true">${icon("grid")}</span><strong>Crew</strong><span>工作区</span>
            <button class="icon-button tabs-sidebar-close" type="button" data-action="mobile-nav" title="关闭项目导航" aria-label="关闭项目导航">${icon("close")}</button>
          </div>
          <div class="tabs-project-nav" aria-label="项目区域">
            <button class="tabs-side-label" type="button" data-action="task-list">${icon("grid")}工作台</button>
            <button class="tabs-side-label" type="button" data-action="todo">${icon("check")}我的待办</button>
          </div>
          <div class="tabs-side-section"><span class="tabs-side-caption">Leader</span>
            <button class="tabs-leader-nav" type="button" data-action="scope" data-scope="general" title="返回 ${esc(data.leader.name)} 的总会话">
              <span class="avatar tabs-side-avatar" aria-hidden="true">${esc(leaderName.slice(0, 1))}</span>
              <span><strong>${esc(leaderName)}</strong><small>${esc(data.leader.runtime)}</small></span>
            </button>
            <p class="tabs-side-note">总会话与任务在上方页签切换</p>
          </div>
          <div class="tabs-side-footer"><button type="button" data-action="assistant">${icon("chat")}全局助手</button><button type="button" data-action="preferences">${icon("more")}设置与偏好</button></div>
        </aside>
        <main class="tabs-main">
          <header class="tabs-main-header" data-main-header>
            <button class="icon-button tabs-menu-button" type="button" data-action="mobile-nav" title="项目导航" aria-label="打开项目导航">${icon("menu")}</button>
            <button class="tabs-identity" type="button" data-action="details" title="${esc(data.leader.name)} · 查看详情" aria-label="查看 ${esc(data.leader.name)} 的详情">
              <span class="avatar tabs-header-avatar" aria-hidden="true">${esc(leaderName.slice(0, 1))}</span>
              <span class="tabs-leader-name">${esc(leaderName)}</span>
            </button>
            <nav class="tabs-track" role="tablist" aria-label="会话页签">${tabs}</nav>
            <button class="icon-button tabs-add-button" type="button" data-action="task-list" title="查找或切换任务" aria-label="打开任务清单">${icon("plus")}</button>
            <div class="header-utilities tabs-utilities">
              ${activeTask && state.status === "queued" ? `<button class="button tabs-start-button" type="button" data-action="start" title="开始推进当前任务"><span>开始推进</span>${icon("arrow-right")}</button>` : ""}
              <button class="icon-button tabs-details-button" type="button" data-action="details" title="查看会话详情与任务资料" aria-label="查看会话详情与任务资料" aria-pressed="${Boolean(state.details)}">${icon("info")}</button>
              <button class="icon-button" type="button" data-action="more" title="更多会话操作" aria-label="更多会话操作">${icon("more")}</button>
            </div>
          </header>
          <section class="tabs-conversation" id="tabs-conversation" role="tabpanel" aria-labelledby="tabs-scope-${esc(state.scope)}">
            <div class="conversation-slot">${conversation}</div>
          </section>
        </main>
      </div>`;
    }
  };
})();
