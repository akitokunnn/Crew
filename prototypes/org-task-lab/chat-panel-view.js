(function (root) {
  "use strict";

  root.CrewChatOrgView = function chatOrgView(ctx) {
    const { leader, people, tasks, links, esc, avatar, statusBadge, runtimeName } = ctx;
    const sameId = (left, right) => String(left ?? "") === String(right ?? "");
    const personById = id => people.find(person => sameId(person.id, id));
    const personTaskCount = id => new Set(links.filter(link => !link.history && sameId(link.personId, id)).map(link => String(link.taskId))).size;
    const selected = id => sameId(ctx.selectedPersonId, id);

    function memberNode(link) {
      const person = personById(link.personId);
      if (!person) return "";
      const otherTasks = Math.max(0, personTaskCount(person.id) - 1);
      const current = sameId(ctx.currentPersonId, person.id) && sameId(ctx.currentTaskId, link.taskId);
      return `<button type="button" class="ctp-person${selected(person.id) ? " ctp-person-selected" : ""}${current ? " ctp-person-current" : ""}"
          data-panel-action="select-person" data-person="${esc(person.id)}" data-task="${esc(link.taskId)}"
          aria-pressed="${selected(person.id)}" aria-label="查看${esc(person.name)}在本任务中的分工">
        ${avatar(person)}
        <span class="ctp-person-copy">
          <span class="ctp-person-heading">
            <span class="ctp-person-name">${esc(person.name)}</span>
            <span class="org-runtime">${esc(runtimeName(link.runtime))}</span>
            ${sameId(person.id, leader.id) ? '<span class="ctp-person-role">本人</span>' : ""}
            ${current ? '<span class="ctp-chat-marker">正在聊</span>' : ""}
          </span>
          <span class="ctp-work">${esc(link.work || "分工待补充")}</span>
          <span class="ctp-person-meta">
            ${statusBadge(link.state)}
            ${otherTasks ? `<span class="ctp-other-tasks">另 ${otherTasks} 项任务</span>` : ""}
          </span>
        </span>
      </button>`;
    }

    function collapsedPeople(taskLinks) {
      const unique = [...new Map(taskLinks.map(link => [String(link.personId), link])).values()];
      return `<div class="ctp-collapsed-people" aria-label="参与人员">
        ${unique.length ? unique.map(link => {
          const person = personById(link.personId);
          if (!person) return "";
          return `<button type="button" class="ctp-mini-person${selected(person.id) ? " ctp-mini-selected" : ""}"
              data-panel-action="select-person" data-person="${esc(person.id)}" data-task="${esc(link.taskId)}"
              aria-pressed="${selected(person.id)}" aria-label="查看${esc(person.name)}的分工">
            ${avatar(person)}<span>${esc(person.name)}</span>
          </button>`;
        }).join("") : '<span class="ctp-empty-members">尚未分配成员</span>'}
      </div>`;
    }

    function taskBranch(task) {
      const taskLinks = links.filter(link => sameId(link.taskId, task.id));
      const expanded = ctx.expanded.has(task.id) || ctx.expanded.has(String(task.id));
      const current = sameId(ctx.currentTaskId, task.id);
      const connected = taskLinks.some(link => selected(link.personId));
      return `<section class="ctp-branch${current ? " ctp-branch-current" : ""}${connected ? " ctp-branch-connected" : ""}"
          aria-label="${esc(task.title)}">
        <div class="ctp-task-node">
          <div class="ctp-task-actions">
            <button type="button" class="ctp-task-toggle" data-panel-action="toggle-task" data-task="${esc(task.id)}"
                aria-expanded="${expanded}" aria-label="${expanded ? "收起" : "展开"}${esc(task.title)}的成员">
              <span class="ctp-task-caption"><span>${esc(task.code)}</span>${current ? '<span class="ctp-current-label">当前对话</span>' : ""}</span>
              <span class="ctp-task-title">${esc(task.title)}</span>
              <span class="ctp-task-state">${statusBadge(task.status)}<span class="ctp-disclosure" aria-hidden="true">${expanded ? "−" : "+"}</span></span>
            </button>
            <button type="button" class="ctp-task-chat" data-panel-action="chat" data-person="${esc(leader.id)}" data-task="${esc(task.id)}"
                aria-label="进入${esc(task.title)}的 leader 对话">对话<span aria-hidden="true">↗</span></button>
          </div>
          ${expanded ? "" : collapsedPeople(taskLinks)}
        </div>
        ${expanded ? `<div class="ctp-task-people">${taskLinks.length ? taskLinks.map(memberNode).join("") : '<p class="ctp-empty-members">尚未分配成员</p>'}</div>` : ""}
      </section>`;
    }

    const unassigned = people.filter(person => !sameId(person.id, leader.id) && !links.some(link => !link.history && sameId(link.personId, person.id)));
    return `<div class="ctp-view" aria-label="本组任务与成员关系">
      <button type="button" class="ctp-leader${selected(leader.id) ? " ctp-leader-selected" : ""}"
          data-panel-action="select-person" data-person="${esc(leader.id)}" data-task="${esc(ctx.currentTaskId || "")}" aria-pressed="${selected(leader.id)}">
        ${avatar(leader)}
        <span class="ctp-leader-copy"><span class="ctp-leader-heading"><strong>${esc(leader.name)}</strong><span>leader</span><span class="org-runtime">${esc(runtimeName(leader.runtime))}</span></span>
          <span class="ctp-leader-summary">${esc(leader.summary || "协调本组任务与验收")}</span></span>
        <span class="ctp-leader-action" aria-hidden="true">›</span>
      </button>
      <div class="ctp-branches">${tasks.length ? tasks.map(taskBranch).join("") : '<p class="ctp-no-tasks">当前范围内没有任务。</p>'}</div>
      ${ctx.scope === "group" && unassigned.length ? `<section class="ctp-standby" aria-label="当前未分配任务的成员"><h3>组内待命<span>${unassigned.length} 人</span></h3>
        <div class="ctp-standby-people">${unassigned.map(person => `<button type="button" class="ctp-standby-person${selected(person.id) ? " ctp-person-selected" : ""}"
            data-panel-action="select-person" data-person="${esc(person.id)}" data-task="" aria-pressed="${selected(person.id)}">
          ${avatar(person)}<span>${esc(person.name)}</span><span class="org-runtime">${esc(runtimeName(person.runtime))}</span>
        </button>`).join("")}</div>
      </section>` : ""}
    </div>`;
  };
})(globalThis);
