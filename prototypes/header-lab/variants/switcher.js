"use strict";
(() => {
  window.HeaderLabVariants = window.HeaderLabVariants || {};
  window.HeaderLabVariants.switcher = {
    id:"switcher", name:"单行上下文切换",
    render(ctx) {
      const {state, data, esc, icon, task, currentTitle, conversation, statusHtml} = ctx;
      return `<div class="variant-layout switcher-layout ${state.mobileNav ? "switcher-nav-open" : ""}">
        ${state.mobileNav ? '<button class="switcher-mask" data-action="mobile-nav" aria-label="关闭项目导航"></button>' : ""}
        <aside class="switcher-sidebar" data-variant-nav tabindex="-1" aria-label="项目导航">
          <div class="switcher-project">${icon("grid")}<strong>${esc(data.projectName)}</strong><button class="icon-button mobile-only" data-action="mobile-nav" aria-label="关闭项目导航">${icon("close")}</button></div>
          <nav class="switcher-navigation" aria-label="工作区"><button data-action="task-list">${icon("grid")}工作台</button><button data-action="scope" data-scope="general">${icon("chat")}交流区</button></nav>
          <p class="switcher-group-label">Leader</p>
          <button class="switcher-leader" data-action="details"><span class="avatar">M</span><span><strong>${esc(data.leader.name)}</strong><small>${esc(data.leader.runtime)}</small></span></button>
          <div class="switcher-sidebar-bottom sidebar-utility-links"><button data-action="assistant">${icon("chat")}全局助手</button><button data-action="preferences">${icon("more")}设置与偏好</button></div>
        </aside>
        <main class="switcher-main">
          <header class="switcher-header" data-main-header>
            <button class="icon-button mobile-only" data-action="mobile-nav" aria-label="打开项目导航">${icon("menu")}</button>
            <button class="switcher-owner desktop-only" data-action="details" title="${esc(data.leader.name)}"><span class="avatar">M</span><span>${esc(data.leader.shortName)}</span></button>
            <span class="switcher-divider desktop-only" aria-hidden="true">/</span>
            <button class="switcher-current" data-action="scope-switch" aria-haspopup="dialog" title="${esc(task ? currentTitle : "总会话")}" aria-label="切换会话，当前${esc(task ? currentTitle : "总会话")}"><h1>${esc(task ? currentTitle : "总会话")}</h1>${icon("chevron")}</button>
            ${statusHtml}
            <div class="header-utilities"><button class="icon-button" data-action="details" aria-label="打开任务资料" title="任务资料">${icon("info")}</button><button class="icon-button" data-action="more" aria-label="更多会话操作">${icon("more")}</button></div>
          </header>
          <div class="conversation-slot">${conversation}</div>
        </main>
      </div>`;
    }
  };
})();
