"use strict";
// Workspace entry page ("/" without a project). Rendered by app.js on every state poll;
// the returned HTML must be a pure function of state and this module's cached activity.
(() => {
  function render(state, {esc, ic, display}) {
    return `<div class="workspace-home"><span class="workspace-logo" aria-hidden="true">${CrewTheme.logo()}</span><h1>Crew 工作区</h1><a class="btn" href="/assistant.html">全局助手</a><button data-local-control class="btn ghost" data-act="settings">设置</button><p class="note">选一个项目，继续聊。</p><div class="list">${state.projects.map(item => `<a class="li project-entry" style="${CrewTheme.projectStyle(state.themes, item.theme)}" href="/?project=${encodeURIComponent(item.name)}"><span class="project-logo" aria-hidden="true">${CrewTheme.logo()}</span><span class="grow">${esc(display(item))}</span><span class="project-theme-name">${esc(CrewTheme.get(state.themes, item.theme).name)}</span>${ic("right")}</a>`).join("")}</div></div>`;
  }
  window.CrewWorkspaceEntry = {render};
})();
